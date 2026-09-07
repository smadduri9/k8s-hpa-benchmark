#!/usr/bin/env python3
"""Approximate /cpu latency SLI brackets from Locust percentile grid (no interpolation)."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_ANALYSIS_DIR = Path(__file__).resolve().parent
if str(_ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS_DIR))

from ingest_locust import PERCENTILE_COLUMNS, PERCENTILE_GRID

CPU_ENDPOINT = "/cpu?intensity=low"
THRESHOLDS_MS = (100, 250, 500, 1000, 2500, 5000)
HEADLINE_THRESHOLD_MS = 500
SLO_TARGET = 0.99

REP_DIR_RE = re.compile(r"^rep-(\d+)$")


@dataclass(frozen=True)
class CpuRow:
    request_count: int
    min_ms: float
    max_ms: float
    percentiles: dict[str, float]


@dataclass(frozen=True)
class FasterBracket:
    threshold_ms: int
    p_lo: str | None
    p_hi: str | None
    min_ms: float
    max_ms: float
    note: str | None = None


def discover_repetitions(run_root: Path) -> list[tuple[int, Path]]:
    reps: list[tuple[int, Path]] = []
    for child in sorted(run_root.iterdir()):
        if not child.is_dir():
            continue
        match = REP_DIR_RE.match(child.name)
        if match:
            reps.append((int(match.group(1)), child))
    return reps


def load_cpu_row(stats_path: Path) -> CpuRow:
    with stats_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("Type") != "GET" or row.get("Name") != CPU_ENDPOINT:
                continue
            percentiles = {}
            for col in PERCENTILE_COLUMNS:
                raw = row.get(col, "")
                if raw in ("", None):
                    raise ValueError(f"missing percentile column {col} in {stats_path}")
                percentiles[col] = float(raw)
            return CpuRow(
                request_count=int(row["Request Count"]),
                min_ms=float(row["Min Response Time"]),
                max_ms=float(row["Max Response Time"]),
                percentiles=percentiles,
            )
    raise FileNotFoundError(f"no GET,{CPU_ENDPOINT} row in {stats_path}")


def _percentile_label(col: str) -> float:
    return float(col.rstrip("%")) / 100.0


def bracket_faster_than(cpu: CpuRow, threshold_ms: float) -> FasterBracket:
    if cpu.min_ms > threshold_ms:
        return FasterBracket(
            threshold_ms=int(threshold_ms),
            p_lo=None,
            p_hi=None,
            min_ms=cpu.min_ms,
            max_ms=cpu.max_ms,
            note="0% (Min Response Time > threshold)",
        )

    if cpu.percentiles["100%"] <= threshold_ms:
        return FasterBracket(
            threshold_ms=int(threshold_ms),
            p_lo="100%",
            p_hi=None,
            min_ms=cpu.min_ms,
            max_ms=cpu.max_ms,
            note="100% (100% column <= threshold)",
        )

    p_lo: str | None = None
    for col in PERCENTILE_COLUMNS:
        if cpu.percentiles[col] <= threshold_ms:
            p_lo = col

    p_hi: str | None = None
    for col in PERCENTILE_COLUMNS:
        if cpu.percentiles[col] > threshold_ms:
            p_hi = col
            break

    if p_lo is None:
        return FasterBracket(
            threshold_ms=int(threshold_ms),
            p_lo=None,
            p_hi=p_hi,
            min_ms=cpu.min_ms,
            max_ms=cpu.max_ms,
            note="fewer than 50%",
        )

    return FasterBracket(
        threshold_ms=int(threshold_ms),
        p_lo=p_lo,
        p_hi=p_hi,
        min_ms=cpu.min_ms,
        max_ms=cpu.max_ms,
    )


def format_bracket(bracket: FasterBracket) -> str:
    if bracket.note:
        return bracket.note
    if bracket.p_lo and bracket.p_hi:
        return f">{bracket.p_lo} and <={bracket.p_hi}"
    if bracket.p_lo and not bracket.p_hi:
        return f">{bracket.p_lo}"
    if bracket.p_hi and not bracket.p_lo:
        return f"<={bracket.p_hi}"
    return "MISSING"


def _format_pct(fraction: float) -> str:
    pct = fraction * 100.0
    if pct >= 1.0:
        return f"{pct:.0f}%"
    if pct >= 0.1:
        return f"{pct:.1f}%"
    return f"{pct:.2f}%"


def miss_bracket(good: FasterBracket) -> str:
    if good.note == "0% (Min Response Time > threshold)":
        return "100%"
    if good.note == "100% (100% column <= threshold)":
        return "0%"
    if good.note == "fewer than 50%":
        return ">= 50%"
    if good.p_lo and good.p_hi:
        lo = 1.0 - _percentile_label(good.p_hi)
        hi = 1.0 - _percentile_label(good.p_lo)
        return f">={_format_pct(lo)} and <{_format_pct(hi)}"
    return "MISSING"


def budget_consumed_bracket(miss_text: str, slo_target: float) -> str:
    allowed = 1.0 - slo_target
    if miss_text == "0%":
        return "0%"
    if miss_text == "100%":
        return "MISSING"
    if miss_text == ">= 50%":
        return f">={_format_pct(0.50 / allowed)} of budget"
    if miss_text.startswith(">=") and " and <" in miss_text:
        low_str, high_str = miss_text.split(" and ")
        low = float(low_str.lstrip(">=").rstrip("%")) / 100.0
        high = float(high_str.lstrip("<").rstrip("%")) / 100.0
        return (
            f">={_format_pct(low / allowed)} and <{_format_pct(high / allowed)} of budget"
        )
    return "MISSING"


def exhaust_days_bracket(miss_text: str, slo_target: float, window_days: float = 30.0) -> str:
    allowed = 1.0 - slo_target
    if miss_text == "0%":
        return "no exhaust (0% miss)"
    if miss_text == "100%":
        return "immediate exhaust"
    if miss_text == ">= 50%":
        upper_days = window_days * allowed / 0.50
        return f"<={upper_days:.2f} days at >=50% miss"
    if miss_text.startswith(">=") and " and <" in miss_text:
        low_str, high_str = miss_text.split(" and ")
        low = float(low_str.lstrip(">=").rstrip("%")) / 100.0
        high = float(high_str.lstrip("<").rstrip("%")) / 100.0
        if low <= 0:
            return "MISSING"
        days_at_low = window_days * allowed / low
        days_at_high = window_days * allowed / high
        return f"{days_at_high:.2f}–{days_at_low:.2f} days (constant miss bracket)"
    return "MISSING"


def process_arm(rep_dir: Path, arm: str, shape: str, rep_num: int) -> None:
    stats_path = rep_dir / f"locust_{arm}_stats.csv"
    cpu = load_cpu_row(stats_path)
    print(
        f"SLI_LOCUST_GRID shape={shape} rep={rep_num} arm={arm} "
        f"method=approximate no_interpolation endpoint=GET,{CPU_ENDPOINT} "
        f"request_count={cpu.request_count}"
    )
    for threshold in THRESHOLDS_MS:
        bracket = bracket_faster_than(cpu, threshold)
        faster = format_bracket(bracket)
        miss = miss_bracket(bracket)
        print(
            f"THRESHOLD_MS={threshold} faster_than={faster} "
            f"miss_rate={miss} min_ms={cpu.min_ms:g} max_ms={cpu.max_ms:g}"
        )
        if threshold == HEADLINE_THRESHOLD_MS:
            print(
                f"SLO_TARGET={SLO_TARGET} allowed_miss_rate={1.0 - SLO_TARGET:g} "
                f"budget_consumed={budget_consumed_bracket(miss, SLO_TARGET)} "
                f"exhaust_30d={exhaust_days_bracket(miss, SLO_TARGET)}"
            )


def process_run(run_root: Path) -> int:
    if not run_root.is_dir():
        print(f"SLI_RUN_ROOT_MISSING path={run_root}", file=sys.stderr)
        return 1
    shape = run_root.name
    reps = discover_repetitions(run_root)
    if not reps:
        print(f"SLI_NO_REPS path={run_root}", file=sys.stderr)
        return 1
    for rep_num, rep_dir in reps:
        for arm in ("fixed", "hpa"):
            process_arm(rep_dir, arm, shape, rep_num)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        type=Path,
        help="Single run root (e.g. results/runs/run-20260905T220046Z-hybrid)",
    )
    parser.add_argument(
        "--hybrid-dir",
        type=Path,
        default=Path("results/runs/run-20260905T220046Z-hybrid"),
    )
    parser.add_argument(
        "--constant-dir",
        type=Path,
        default=Path("results/runs/run-20260906T050515Z-constant"),
    )
    args = parser.parse_args()
    if args.run_dir:
        return process_run(args.run_dir)
    rc = process_run(args.hybrid_dir)
    if rc != 0:
        return rc
    return process_run(args.constant_dir)


if __name__ == "__main__":
    raise SystemExit(main())
