#!/usr/bin/env python3
"""Count scaling events from in-run replica_series_*.csv (spec_replicas deltas)."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

REP_DIR_RE = re.compile(r"^rep-(\d+)$")


@dataclass(frozen=True)
class ScalingCounts:
    scale_ups: int
    scale_downs: int
    total_events: int
    replica_delta: int


def discover_repetitions(run_root: Path) -> list[tuple[int, Path]]:
    reps: list[tuple[int, Path]] = []
    for child in sorted(run_root.iterdir()):
        if not child.is_dir():
            continue
        match = REP_DIR_RE.match(child.name)
        if match:
            reps.append((int(match.group(1)), child))
    return reps


def load_spec_series(path: Path) -> list[int]:
    specs: list[int] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            specs.append(int(row["spec_replicas"]))
    return specs


def count_scaling_events(specs: list[int]) -> ScalingCounts:
    if len(specs) < 2:
        return ScalingCounts(0, 0, 0, 0)
    scale_ups = 0
    scale_downs = 0
    replica_delta = 0
    for prev, curr in zip(specs[:-1], specs[1:]):
        if curr > prev:
            scale_ups += 1
        elif curr < prev:
            scale_downs += 1
        replica_delta += abs(curr - prev)
    return ScalingCounts(scale_ups, scale_downs, scale_ups + scale_downs, replica_delta)


def median_counts(values: list[ScalingCounts]) -> ScalingCounts:
    if not values:
        return ScalingCounts(0, 0, 0, 0)
    return ScalingCounts(
        int(np.median([v.scale_ups for v in values])),
        int(np.median([v.scale_downs for v in values])),
        int(np.median([v.total_events for v in values])),
        int(np.median([v.replica_delta for v in values])),
    )


def print_counts(label: str, counts: ScalingCounts) -> None:
    print(
        f"{label} scale_ups={counts.scale_ups} scale_downs={counts.scale_downs} "
        f"total_events={counts.total_events} replica_delta={counts.replica_delta}"
    )


def process_run(run_root: Path) -> int:
    if not run_root.is_dir():
        print(f"SCALING_RUN_ROOT_MISSING path={run_root}", file=sys.stderr)
        return 1
    shape = run_root.name
    reps = discover_repetitions(run_root)
    if not reps:
        print(f"SCALING_NO_REPS path={run_root}", file=sys.stderr)
        return 1

    per_arm: dict[str, list[ScalingCounts]] = {"fixed": [], "hpa": []}
    for rep_num, rep_dir in reps:
        for arm in ("fixed", "hpa"):
            series_path = rep_dir / f"replica_series_{arm}.csv"
            specs = load_spec_series(series_path)
            counts = count_scaling_events(specs)
            print_counts(f"SCALING shape={shape} rep={rep_num} arm={arm}", counts)
            per_arm[arm].append(counts)

    n = len(reps)
    for arm in ("fixed", "hpa"):
        med = median_counts(per_arm[arm])
        print_counts(f"SCALING_MEDIAN shape={shape} n={n} arm={arm}", med)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--arm", choices=("fixed", "hpa"), help="unused; kept for verify CLI")
    parser.add_argument("--rep", type=int, help="unused; kept for verify CLI")
    args = parser.parse_args()
    return process_run(args.run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
