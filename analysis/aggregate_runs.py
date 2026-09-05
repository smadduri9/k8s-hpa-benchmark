#!/usr/bin/env python3
"""Aggregate metrics across rep-N directories under a run root.

Median and IQR are the primary outputs; paired Wilcoxon (fixed vs hpa) is
supporting evidence. n is the count of complete repetitions, never assumed.
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

REQUIRED_FILES = (
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


def repetition_complete(rep_dir: Path) -> tuple[bool, list[str]]:
    missing = [name for name in REQUIRED_FILES if not (rep_dir / name).is_file()]
    return not missing, missing


def load_rep_metrics(rep_dir: Path) -> dict[str, dict[str, float]]:
    fixed_stats = load_locust_aggregated_stats(str(rep_dir / "locust_fixed_stats.csv"))
    hpa_stats = load_locust_aggregated_stats(str(rep_dir / "locust_hpa_stats.csv"))

    fixed_pod_hours = pod_hours_from_replica_series(str(rep_dir / "replica_series_fixed.csv"))
    hpa_pod_hours = pod_hours_from_replica_series(str(rep_dir / "replica_series_hpa.csv"))

    fixed_successful = successful_requests_from_locust_stats(
        str(rep_dir / "locust_fixed_stats.csv")
    )
    hpa_successful = successful_requests_from_locust_stats(str(rep_dir / "locust_hpa_stats.csv"))

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

    shape = read_shape(run_root)
    included: list[int] = []
    excluded: list[tuple[int, list[str]]] = []
    per_rep: dict[int, dict[str, dict[str, float]]] = {}

    for rep_num, rep_dir in discover_repetitions(run_root):
        complete, missing = repetition_complete(rep_dir)
        if not complete:
            excluded.append((rep_num, missing))
            continue
        included.append(rep_num)
        per_rep[rep_num] = load_rep_metrics(rep_dir)

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
        f"AGGREGATE_N shape={shape} n={n} "
        f"reps_included={included_list} reps_excluded={excluded_list}"
    )

    if n == 0:
        print("AGGREGATE_NO_COMPLETE_REPETITIONS", file=sys.stderr)
        return 1

    for metric in METRICS:
        for arm in ("fixed", "hpa"):
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

    for metric in METRICS:
        fixed_vals = np.asarray(
            [per_rep[rep]["fixed"][metric] for rep in included], dtype=np.float64
        )
        hpa_vals = np.asarray(
            [per_rep[rep]["hpa"][metric] for rep in included], dtype=np.float64
        )
        if np.any(np.isnan(fixed_vals)) or np.any(np.isnan(hpa_vals)):
            print(
                f"WILCOXON metric={metric} n={n} w={MISSING} "
                f"p_two_sided={MISSING} reason=non_finite_values"
            )
            print(
                f"P_FLOOR n={n} min_attainable_two_sided_p={p_floor(n):.6f} "
                f"significant_at_0.05_possible={str(p_floor(n) <= 0.05).lower()}"
            )
            continue

        if n < 2:
            print(
                f"WILCOXON metric={metric} n={n} w={MISSING} "
                f"p_two_sided={MISSING} test_possible=false "
                f"reason=insufficient_pairs"
            )
            print(
                f"P_FLOOR n={n} min_attainable_two_sided_p={p_floor(n):.6f} "
                f"significant_at_0.05_possible={str(p_floor(n) <= 0.05).lower()}"
            )
            if metric in LATENCY_METRICS:
                slower = int(hpa_vals[0] > fixed_vals[0])
                print(
                    f"DIRECTION_CONSISTENCY metric={metric} "
                    f"hpa_slower_in={slower}/{n}"
                )
            continue

        differences = hpa_vals - fixed_vals
        result = wilcoxon_signed_rank(differences)
        print(
            f"WILCOXON metric={metric} n={result.n_effective} "
            f"w={result.statistic:g} p_two_sided={result.p_value:.6f} "
            f"method={result.method} ties={str(result.ties_present).lower()}"
        )
        for line in format_result_lines(result, metric):
            if line.startswith("P_FLOOR"):
                print(line)
                break

        if metric in LATENCY_METRICS:
            slower = int(np.sum(hpa_vals > fixed_vals))
            print(
                f"DIRECTION_CONSISTENCY metric={metric} hpa_slower_in={slower}/{n}"
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
