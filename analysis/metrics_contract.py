"""
Shared metrics CSV contract: required columns and minimum population coverage.

Published analysis window: every row from LOAD_START t0 through t0+RUN_TIME is
included in CSV output and coverage assessment. No warmup or edge rows are dropped.
"""

from __future__ import annotations

MISSING = "MISSING"

# Prometheus rate(...[1m]) needs samples before t0; collectors query from t0 minus
# this offset but only publish rows in [t0, t0+RUN_TIME]. Not used to exclude rows.
METRIC_QUERY_PREROLL_SEC = 60

# Minimum fraction of rows that must be non-MISSING per required value column.
MIN_COLUMN_COVERAGE_RATIO = 0.95

METRICS_COVERAGE_BELOW_THRESHOLD = "METRICS_COVERAGE_BELOW_THRESHOLD"

REQUIRED_VALUE_COLUMNS = [
    "cpu_utilization_pct",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "rps",
    "replicas",
    "error_rate",
]


def column_coverage(rows: list[dict], column: str) -> tuple[int, int, float]:
    total = len(rows)
    if total == 0:
        return 0, 0, 0.0
    populated = sum(1 for row in rows if row.get(column) not in (MISSING, "", None))
    return populated, total, populated / total


def print_column_coverage_summary(rows: list[dict], label: str = "") -> None:
    prefix = f"{label} " if label else ""
    for column in REQUIRED_VALUE_COLUMNS:
        populated, total, ratio = column_coverage(rows, column)
        print(
            f"METRICS_COLUMN_COVERAGE {prefix}column={column} "
            f"populated={populated}/{total} ratio={ratio:.4f}"
        )


def assert_column_coverage(
    rows: list[dict],
    *,
    threshold: float = MIN_COLUMN_COVERAGE_RATIO,
    label: str = "",
) -> None:
    if not rows:
        raise RuntimeError("ASSERTION FAILED: metrics CSV has zero rows")

    print_column_coverage_summary(rows, label=label)

    for column in REQUIRED_VALUE_COLUMNS:
        populated, total, ratio = column_coverage(rows, column)
        if populated == 0:
            raise RuntimeError(f"ASSERTION FAILED: required column {column} has zero populated rows")
        if ratio < threshold:
            raise RuntimeError(
                f"{METRICS_COVERAGE_BELOW_THRESHOLD} column={column} "
                f"populated={populated}/{total} ratio={ratio:.4f} threshold={threshold:.2f}"
            )
