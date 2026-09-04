"""
Shared metrics CSV contract: required columns and minimum population coverage.
"""

from __future__ import annotations

MISSING = "MISSING"

# Prometheus rate()[1m] needs history before the first sample; trailing partial buckets excluded.
METRIC_RATE_WARMUP_SEC = 60
METRIC_SCRAPE_SETTLE_SEC = 15

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


def assessable_rows(rows: list[dict], step_sec: int = 15) -> list[dict]:
    """Rows eligible for coverage assessment (excludes rate warmup and scrape settle edges)."""
    if not rows:
        return rows
    warmup_rows = (METRIC_RATE_WARMUP_SEC + step_sec - 1) // step_sec
    settle_rows = (METRIC_SCRAPE_SETTLE_SEC + step_sec - 1) // step_sec
    end_idx = len(rows) - settle_rows if settle_rows else len(rows)
    if end_idx <= warmup_rows:
        return rows
    return rows[warmup_rows:end_idx]


def column_coverage(rows: list[dict], column: str) -> tuple[int, int, float]:
    total = len(rows)
    if total == 0:
        return 0, 0, 0.0
    populated = sum(1 for row in rows if row.get(column) not in (MISSING, "", None))
    return populated, total, populated / total


def print_column_coverage_summary(rows: list[dict], label: str = "", *, step_sec: int = 15) -> None:
    assessed = assessable_rows(rows, step_sec=step_sec)
    prefix = f"{label} " if label else ""
    for column in REQUIRED_VALUE_COLUMNS:
        populated, total, ratio = column_coverage(assessed, column)
        print(
            f"METRICS_COLUMN_COVERAGE {prefix}column={column} "
            f"populated={populated}/{total} ratio={ratio:.4f}"
        )


def assert_column_coverage(
    rows: list[dict],
    *,
    threshold: float = MIN_COLUMN_COVERAGE_RATIO,
    label: str = "",
    step_sec: int = 15,
) -> None:
    if not rows:
        raise RuntimeError("ASSERTION FAILED: metrics CSV has zero rows")

    assessed = assessable_rows(rows, step_sec=step_sec)
    if not assessed:
        raise RuntimeError("ASSERTION FAILED: no assessable rows after warmup/settle exclusion")

    print_column_coverage_summary(rows, label=label, step_sec=step_sec)

    for column in REQUIRED_VALUE_COLUMNS:
        populated, total, ratio = column_coverage(assessed, column)
        if populated == 0:
            raise RuntimeError(f"ASSERTION FAILED: required column {column} has zero populated rows")
        if ratio < threshold:
            raise RuntimeError(
                f"{METRICS_COVERAGE_BELOW_THRESHOLD} column={column} "
                f"populated={populated}/{total} ratio={ratio:.4f} threshold={threshold:.2f}"
            )
