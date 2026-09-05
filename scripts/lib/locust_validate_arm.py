#!/usr/bin/env python3
"""Validate Locust arm artifacts after a bounded run."""

from __future__ import annotations

import csv
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: locust_validate_arm.py <stats_csv> <log_file> <exit_rc>",
            file=sys.stderr,
        )
        return 2

    stats_csv = Path(sys.argv[1])
    log_file = Path(sys.argv[2])
    exit_rc = int(sys.argv[3])

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

    aggregated = None
    with stats_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("Name") == "Aggregated":
                aggregated = row
                break

    if aggregated is None:
        print(f"LOCUST_STATS_INVALID csv={stats_csv} reason=missing_aggregated_row", file=sys.stderr)
        return 1

    try:
        request_count = int(float(aggregated.get("Request Count", "0")))
        failure_count = int(float(aggregated.get("Failure Count", "0")))
    except ValueError:
        print(f"LOCUST_STATS_INVALID csv={stats_csv} reason=non_numeric_counts", file=sys.stderr)
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


if __name__ == "__main__":
    raise SystemExit(main())
