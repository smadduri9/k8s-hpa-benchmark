#!/usr/bin/env python3
"""Aggregate metrics across rep-N directories under a run root.

Median and IQR are the primary outputs; paired Wilcoxon is supporting evidence.
n is the count of complete repetitions, never assumed.

Supports legacy two-arm reps (files at rep root) and Phase 5 three-arm reps
(rep-N/<arm>/...).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ANALYSIS_DIR = Path(__file__).resolve().parent
if str(_ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS_DIR))

import numpy as np

from analyze_results import (
    LIST_PRICE_PER_POD_HOUR,
    load_locust_aggregated_stats,
    pod_hours_from_replica_series,
    successful_requests_from_locust_stats,
)
from wilcoxon import format_result_lines, p_floor, wilcoxon_signed_rank

MISSING = "MISSING"

REP_DIR_RE = re.compile(r"^rep-(\d+)$")

LEGACY_ARMS = ("fixed", "hpa")
PHASE5_ARMS = ("fixed", "hpa_tuned", "hpa_stock")

LEGACY_REQUIRED_FILES = (
    "locust_fixed_stats.csv",
    "locust_hpa_stats.csv",
    "replica_series_fixed.csv",
    "replica_series_hpa.csv",
)

METRICS = (
    "client_p50_ms",
    "client_p95_ms",
    "client_p99_ms",
    "failure_rate",
    "pod_hours",
    "cost_per_1k_successful",
)

LATENCY_METRICS = frozenset({"client_p50_ms", "client_p95_ms", "client_p99_ms"})


def discover_repetitions(run_root: Path) -> list[tuple[int, Path]]:
    reps: list[tuple[int, Path]] = []
    for child in sorted(run_root.iterdir()):
        if not child.is_dir():
            continue
        match = REP_DIR_RE.match(child.name)
        if match:
            reps.append((int(match.group(1)), child))
    return reps


def detect_rep_layout(rep_dir: Path) -> str:
    if (rep_dir / "locust_fixed_stats.csv").is_file():
        return "legacy"
    return "phase5"


def discover_run_layout(run_root: Path) -> tuple[str, list[str]]:
    reps = discover_repetitions(run_root)
    if not reps:
        return "unknown", []

    layouts = {detect_rep_layout(rep_dir) for _, rep_dir in reps}
    if layouts == {"legacy"}:
        return "legacy", list(LEGACY_ARMS)

    arms_found: set[str] = set()
    for _, rep_dir in reps:
        for arm in PHASE5_ARMS:
            if (rep_dir / arm).is_dir():
                arms_found.add(arm)
    ordered = [arm for arm in PHASE5_ARMS if arm in arms_found]
    return "phase5", ordered


def collect_mode_for_arm(arm: str) -> str:
    return "fixed" if arm == "fixed" else "hpa"


def arm_required_files(arm: str) -> tuple[str, ...]:
    mode = collect_mode_for_arm(arm)
    return (f"locust_{arm}_stats.csv", f"replica_series_{mode}.csv")


def repetition_complete(
    rep_dir: Path, layout: str, expected_arms: list[str]
) -> tuple[bool, list[str]]:
    if layout == "legacy":
        missing = [
            name for name in LEGACY_REQUIRED_FILES if not (rep_dir / name).is_file()
        ]
        return not missing, missing

    missing: list[str] = []
    for arm in expected_arms:
        arm_dir = rep_dir / arm
        if not arm_dir.is_dir():
            missing.append(f"{arm}/")
            continue
        for fname in arm_required_files(arm):
            if not (arm_dir / fname).is_file():
                missing.append(f"{arm}/{fname}")
    return not missing, missing


def load_arm_metrics(arm_dir: Path, arm: str) -> dict[str, float]:
    locust_path = arm_dir / f"locust_{arm}_stats.csv"
    replica_path = arm_dir / f"replica_series_{collect_mode_for_arm(arm)}.csv"

    stats = load_locust_aggregated_stats(str(locust_path))
    pod_hours = pod_hours_from_replica_series(str(replica_path))
    successful = successful_requests_from_locust_stats(str(locust_path))
    cost_per_k = (
        pod_hours * LIST_PRICE_PER_POD_HOUR / (successful / 1000.0)
        if successful
        else float("nan")
    )

    return {
        "client_p50_ms": stats["p50_ms"],
        "client_p95_ms": stats["p95_ms"],
        "client_p99_ms": stats["p99_ms"],
        "failure_rate": stats["failure_rate"],
        "pod_hours": pod_hours,
        "cost_per_1k_successful": cost_per_k,
    }


def load_rep_metrics(
    rep_dir: Path, layout: str, expected_arms: list[str]
) -> dict[str, dict[str, float]]:
    if layout == "legacy":
        fixed_stats = load_locust_aggregated_stats(str(rep_dir / "locust_fixed_stats.csv"))
        hpa_stats = load_locust_aggregated_stats(str(rep_dir / "locust_hpa_stats.csv"))
        fixed_pod_hours = pod_hours_from_replica_series(
            str(rep_dir / "replica_series_fixed.csv")
        )
        hpa_pod_hours = pod_hours_from_replica_series(
            str(rep_dir / "replica_series_hpa.csv")
        )
        fixed_successful = successful_requests_from_locust_stats(
            str(rep_dir / "locust_fixed_stats.csv")
        )
        hpa_successful = successful_requests_from_locust_stats(
            str(rep_dir / "locust_hpa_stats.csv")
        )
        fixed_cost_per_k = (
            fixed_pod_hours * LIST_PRICE_PER_POD_HOUR / (fixed_successful / 1000.0)
            if fixed_successful
            else float("nan")
        )
        hpa_cost_per_k = (
            hpa_pod_hours * LIST_PRICE_PER_POD_HOUR / (hpa_successful / 1000.0)
            if hpa_successful
            else float("nan")
        )
        return {
            "fixed": {
                "client_p50_ms": fixed_stats["p50_ms"],
                "client_p95_ms": fixed_stats["p95_ms"],
                "client_p99_ms": fixed_stats["p99_ms"],
                "failure_rate": fixed_stats["failure_rate"],
                "pod_hours": fixed_pod_hours,
                "cost_per_1k_successful": fixed_cost_per_k,
            },
            "hpa": {
                "client_p50_ms": hpa_stats["p50_ms"],
                "client_p95_ms": hpa_stats["p95_ms"],
                "client_p99_ms": hpa_stats["p99_ms"],
                "failure_rate": hpa_stats["failure_rate"],
                "pod_hours": hpa_pod_hours,
                "cost_per_1k_successful": hpa_cost_per_k,
            },
        }

    return {
        arm: load_arm_metrics(rep_dir / arm, arm) for arm in expected_arms
    }


def comparison_pairs(layout: str, arms: list[str]) -> list[tuple[str, str]]:
    if layout == "legacy":
        return [("fixed", "hpa")]
    pairs: list[tuple[str, str]] = []
    for index, arm_a in enumerate(arms):
        for arm_b in arms[index + 1 :]:
            pairs.append((arm_a, arm_b))
    return pairs


def median_iqr(values: list[float]) -> tuple[float, float, float, float]:
    arr = np.asarray(values, dtype=np.float64)
    q1, median, q3 = np.percentile(arr, [25, 50, 75])
    return float(median), float(q1), float(q3), float(q3 - q1)


def read_shape(run_root: Path) -> str:
    manifest_path = run_root / "manifest.json"
    if not manifest_path.is_file():
        return MISSING
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return MISSING
    shape = manifest.get("shape")
    return str(shape) if shape is not None else MISSING


def aggregate(run_root: Path) -> int:
    if not run_root.is_dir():
        print(f"AGGREGATE_RUN_ROOT_MISSING path={run_root}", file=sys.stderr)
        return 1

    layout, expected_arms = discover_run_layout(run_root)
    if layout == "unknown" or not expected_arms:
        print("AGGREGATE_NO_REPETITIONS", file=sys.stderr)
        return 1

    shape = read_shape(run_root)
    arms_csv = ",".join(expected_arms)
    included: list[int] = []
    excluded: list[tuple[int, list[str]]] = []
    per_rep: dict[int, dict[str, dict[str, float]]] = {}

    for rep_num, rep_dir in discover_repetitions(run_root):
        if detect_rep_layout(rep_dir) != layout:
            excluded.append((rep_num, ["layout_mismatch"]))
            continue
        complete, missing = repetition_complete(rep_dir, layout, expected_arms)
        if not complete:
            excluded.append((rep_num, missing))
            continue
        included.append(rep_num)
        per_rep[rep_num] = load_rep_metrics(rep_dir, layout, expected_arms)

    n = len(included)
    included_list = ",".join(str(rep) for rep in included) if included else MISSING
    if excluded:
        excluded_parts = [
            f"rep-{rep_num}:{'+'.join(missing)}" for rep_num, missing in excluded
        ]
        excluded_list = ";".join(excluded_parts)
    else:
        excluded_list = MISSING

    print(
        f"AGGREGATE_N shape={shape} layout={layout} arms={arms_csv} n={n} "
        f"reps_included={included_list} reps_excluded={excluded_list}"
    )

    if n == 0:
        print("AGGREGATE_NO_COMPLETE_REPETITIONS", file=sys.stderr)
        return 1

    for metric in METRICS:
        for arm in expected_arms:
            values = [per_rep[rep][arm][metric] for rep in included]
            if any(np.isnan(values)):
                print(
                    f"MEDIAN_IQR metric={metric} arm={arm} "
                    f"median={MISSING} q1={MISSING} q3={MISSING} iqr={MISSING} n={n}"
                )
                continue
            median, q1, q3, iqr = median_iqr(values)
            print(
                f"MEDIAN_IQR metric={metric} arm={arm} "
                f"median={median:.6g} q1={q1:.6g} q3={q3:.6g} iqr={iqr:.6g} n={n}"
            )

    for arm_a, arm_b in comparison_pairs(layout, expected_arms):
        for metric in METRICS:
            a_vals = np.asarray(
                [per_rep[rep][arm_a][metric] for rep in included], dtype=np.float64
            )
            b_vals = np.asarray(
                [per_rep[rep][arm_b][metric] for rep in included], dtype=np.float64
            )
            if np.any(np.isnan(a_vals)) or np.any(np.isnan(b_vals)):
                print(
                    f"WILCOXON metric={metric} arm_a={arm_a} arm_b={arm_b} n={n} "
                    f"w={MISSING} p_two_sided={MISSING} reason=non_finite_values"
                )
                print(
                    f"P_FLOOR arm_a={arm_a} arm_b={arm_b} n={n} "
                    f"min_attainable_two_sided_p={p_floor(n):.6f} "
                    f"significant_at_0.05_possible={str(p_floor(n) <= 0.05).lower()}"
                )
                continue

            if n < 2:
                print(
                    f"WILCOXON metric={metric} arm_a={arm_a} arm_b={arm_b} n={n} "
                    f"w={MISSING} p_two_sided={MISSING} test_possible=false "
                    f"reason=insufficient_pairs"
                )
                print(
                    f"P_FLOOR arm_a={arm_a} arm_b={arm_b} n={n} "
                    f"min_attainable_two_sided_p={p_floor(n):.6f} "
                    f"significant_at_0.05_possible={str(p_floor(n) <= 0.05).lower()}"
                )
                if metric in LATENCY_METRICS:
                    slower = int(b_vals[0] > a_vals[0])
                    print(
                        f"DIRECTION_CONSISTENCY metric={metric} arm_a={arm_a} "
                        f"arm_b={arm_b} arm_b_slower_in={slower}/{n}"
                    )
                continue

            differences = b_vals - a_vals
            result = wilcoxon_signed_rank(differences)
            print(
                f"WILCOXON metric={metric} arm_a={arm_a} arm_b={arm_b} "
                f"n={result.n_effective} w={result.statistic:g} "
                f"p_two_sided={result.p_value:.6f} method={result.method} "
                f"ties={str(result.ties_present).lower()}"
            )
            for line in format_result_lines(result, metric):
                if line.startswith("P_FLOOR"):
                    print(
                        line.replace("P_FLOOR ", f"P_FLOOR arm_a={arm_a} arm_b={arm_b} ")
                    )
                    break

            if metric in LATENCY_METRICS:
                slower = int(np.sum(b_vals > a_vals))
                print(
                    f"DIRECTION_CONSISTENCY metric={metric} arm_a={arm_a} "
                    f"arm_b={arm_b} arm_b_slower_in={slower}/{n}"
                )

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-root",
        required=True,
        help="results/runs/<run_id> directory containing rep-N subdirs",
    )
    args = parser.parse_args()
    return aggregate(Path(args.run_root))


if __name__ == "__main__":
    raise SystemExit(main())
