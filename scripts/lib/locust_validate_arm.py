#!/usr/bin/env python3
"""Validate Locust arm artifacts after a bounded run."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


def read_aggregated_counts(stats_csv: Path) -> tuple[int, int]:
    aggregated = None
    with stats_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("Name") == "Aggregated":
                aggregated = row
                break

    if aggregated is None:
        raise ValueError("missing_aggregated_row")

    try:
        request_count = int(float(aggregated.get("Request Count", "0")))
        failure_count = int(float(aggregated.get("Failure Count", "0")))
    except ValueError:
        raise ValueError("non_numeric_counts") from None

    return request_count, failure_count


def validate_warmup(stats_csv: Path, exit_rc: int) -> int:
    if not stats_csv.is_file() or stats_csv.stat().st_size == 0:
        print(f"LOCUST_WARMUP_STATS_MISSING csv={stats_csv}", file=sys.stderr)
        return 1

    try:
        request_count, failure_count = read_aggregated_counts(stats_csv)
    except ValueError as exc:
        print(
            f"LOCUST_WARMUP_STATS_INVALID csv={stats_csv} reason={exc}",
            file=sys.stderr,
        )
        return 1

    if request_count <= 0:
        print(
            f"LOCUST_WARMUP_STATS_INVALID csv={stats_csv} reason=zero_request_count",
            file=sys.stderr,
        )
        return 1

    print(
        f"LOCUST_WARMUP_ARTIFACTS_VALID requests={request_count} "
        f"failures={failure_count}"
    )
    if exit_rc != 0:
        print(f"LOCUST_NONZERO_EXIT_IGNORED rc={exit_rc} warmup=true")
    return 0


def validate_measured(stats_csv: Path, log_file: Path, exit_rc: int) -> int:
    if not stats_csv.is_file() or stats_csv.stat().st_size == 0:
        print(f"LOCUST_STATS_MISSING csv={stats_csv}", file=sys.stderr)
        return 1

    if not log_file.is_file():
        print(f"LOCUST_SHAPE_INCOMPLETE log={log_file} reason=missing_log", file=sys.stderr)
        return 1

    log_text = log_file.read_text(encoding="utf-8", errors="replace")
    if "Shape test completed" not in log_text:
        print(
            f"LOCUST_SHAPE_INCOMPLETE log={log_file} reason=shape_marker_absent",
            file=sys.stderr,
        )
        return 1

    try:
        request_count, failure_count = read_aggregated_counts(stats_csv)
    except ValueError as exc:
        print(f"LOCUST_STATS_INVALID csv={stats_csv} reason={exc}", file=sys.stderr)
        return 1

    if request_count <= 0:
        print(
            f"LOCUST_STATS_INVALID csv={stats_csv} reason=zero_request_count",
            file=sys.stderr,
        )
        return 1

    fail_ratio = failure_count / request_count
    print(
        f"LOCUST_ARTIFACTS_VALID csv={stats_csv} requests={request_count} "
        f"failures={failure_count} fail_ratio={fail_ratio:.4f}"
    )

    if exit_rc != 0:
        print(f"LOCUST_NONZERO_EXIT_IGNORED rc={exit_rc} fail_ratio={fail_ratio:.4f}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Locust bounded-run artifacts.")
    parser.add_argument("stats_csv", type=Path)
    parser.add_argument("log_file", type=Path)
    parser.add_argument("exit_rc", type=int)
    parser.add_argument(
        "--warmup",
        action="store_true",
        help="Warm-up run: stats CSV only; no LoadTestShape completion marker.",
    )
    args = parser.parse_args()

    if args.warmup:
        return validate_warmup(args.stats_csv, args.exit_rc)
    return validate_measured(args.stats_csv, args.log_file, args.exit_rc)


if __name__ == "__main__":
    raise SystemExit(main())
