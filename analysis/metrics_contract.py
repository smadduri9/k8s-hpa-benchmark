"""
Shared metrics CSV contract: required columns and minimum population coverage.

Published analysis window: every row from LOAD_START t0 through t0+RUN_TIME is
included in CSV output. Rows where ready_replicas == 0 are UNAVAILABLE; metric
cells on those rows are TARGET_UNAVAILABLE. Coverage is assessed over serving
rows (ready_replicas > 0): AVAILABLE and DEGRADED.
"""

from __future__ import annotations

MISSING = "MISSING"
TARGET_UNAVAILABLE = "TARGET_UNAVAILABLE"
AVAILABILITY_UNAVAILABLE = "UNAVAILABLE"
AVAILABILITY_DEGRADED = "DEGRADED"
AVAILABILITY_AVAILABLE = "AVAILABLE"

# Prometheus rate(...[1m]) needs samples before t0; collectors query from t0 minus
# this offset but only publish rows in [t0, t0+RUN_TIME]. Not used to exclude rows.
METRIC_QUERY_PREROLL_SEC = 60

# Minimum fraction of serving-row cells that must be non-MISSING per required column.
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

METRIC_VALUE_COLUMNS = [
    "cpu_utilization_pct",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "rps",
    "error_rate",
]

RATE_DERIVED_COLUMNS = [
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "rps",
    "error_rate",
]

# First N serving rows excluded from rate-column coverage (burst-onset counter semantics).
BURST_ONSET_RATE_ROW_EXCLUSIONS = 2


def row_ready_replicas(row: dict) -> int | None:
    raw = row.get("ready_replicas", row.get("replicas"))
    if raw in (MISSING, TARGET_UNAVAILABLE, "", None):
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def row_availability_state(row: dict, *, declared_replicas: int | None = None) -> str:
    ready = row_ready_replicas(row)
    if ready is None or ready <= 0:
        return AVAILABILITY_UNAVAILABLE
    if declared_replicas is not None and ready < declared_replicas:
        return AVAILABILITY_DEGRADED
    return AVAILABILITY_AVAILABLE


def is_serving_row(row: dict) -> bool:
    ready = row_ready_replicas(row)
    return ready is not None and ready > 0


def is_populated_metric_value(value: object) -> bool:
    return value not in (MISSING, TARGET_UNAVAILABLE, "", None)


def column_coverage(rows: list[dict], column: str) -> tuple[int, int, float]:
    total = len(rows)
    if total == 0:
        return 0, 0, 0.0
    populated = sum(1 for row in rows if is_populated_metric_value(row.get(column)))
    return populated, total, populated / total


def coverage_rows_for_column(
    rows: list[dict],
    column: str,
    *,
    declared_replicas: int | None = None,
) -> list[dict]:
    del declared_replicas  # serving rows only; declared affects row state, not coverage set
    serving_rows = [row for row in rows if is_serving_row(row)]
    if column in RATE_DERIVED_COLUMNS and len(serving_rows) > BURST_ONSET_RATE_ROW_EXCLUSIONS:
        return serving_rows[BURST_ONSET_RATE_ROW_EXCLUSIONS:]
    return serving_rows


def column_coverage_available_rows(
    rows: list[dict],
    column: str,
    *,
    declared_replicas: int | None = None,
) -> tuple[int, int, float]:
    eligible_rows = coverage_rows_for_column(rows, column, declared_replicas=declared_replicas)
    total = len(eligible_rows)
    if total == 0:
        return 0, 0, 0.0
    populated = sum(
        1 for row in eligible_rows if is_populated_metric_value(row.get(column))
    )
    return populated, total, populated / total


def print_target_availability_summary(
    rows: list[dict], label: str = "", *, declared_replicas: int | None = None
) -> None:
    prefix = f"{label} " if label else ""
    total = len(rows)
    unavailable = sum(
        1
        for row in rows
        if row_availability_state(row, declared_replicas=declared_replicas)
        == AVAILABILITY_UNAVAILABLE
    )
    degraded = sum(
        1
        for row in rows
        if row_availability_state(row, declared_replicas=declared_replicas)
        == AVAILABILITY_DEGRADED
    )
    available = sum(
        1
        for row in rows
        if row_availability_state(row, declared_replicas=declared_replicas)
        == AVAILABILITY_AVAILABLE
    )
    serving = degraded + available
    serving_ratio = serving / total if total else 0.0
    print(
        f"TARGET_AVAILABILITY {prefix}rows_unavailable={unavailable}/{total} "
        f"rows_degraded={degraded}/{total} rows_available={available}/{total} "
        f"rows_serving={serving}/{total} ratio={serving_ratio:.4f}"
    )


def print_column_coverage_summary(
    rows: list[dict], label: str = "", *, declared_replicas: int | None = None
) -> None:
    prefix = f"{label} " if label else ""
    for column in REQUIRED_VALUE_COLUMNS:
        populated, total, ratio = column_coverage_available_rows(
            rows, column, declared_replicas=declared_replicas
        )
        print(
            f"METRICS_COLUMN_COVERAGE {prefix}column={column} "
            f"populated={populated}/{total} ratio={ratio:.4f}"
        )


def infer_declared_replicas(rows: list[dict]) -> int | None:
    if not rows or rows[0].get("experiment") != "fixed":
        return None
    specs: list[int] = []
    for row in rows:
        raw = row.get("spec_replicas")
        if raw in (MISSING, TARGET_UNAVAILABLE, "", None):
            continue
        specs.append(int(float(raw)))
    return max(specs) if specs else None


def assert_column_coverage(
    rows: list[dict],
    *,
    threshold: float = MIN_COLUMN_COVERAGE_RATIO,
    label: str = "",
    declared_replicas: int | None = None,
) -> None:
    if not rows:
        raise RuntimeError("ASSERTION FAILED: metrics CSV has zero rows")

    print_target_availability_summary(rows, label=label, declared_replicas=declared_replicas)
    print_column_coverage_summary(rows, label=label, declared_replicas=declared_replicas)

    serving_rows = [row for row in rows if is_serving_row(row)]
    if not serving_rows:
        raise RuntimeError(
            "ASSERTION FAILED: zero serving rows (ready_replicas > 0) in published window"
        )

    for column in REQUIRED_VALUE_COLUMNS:
        populated, total, ratio = column_coverage_available_rows(
            rows, column, declared_replicas=declared_replicas
        )
        if populated == 0:
            raise RuntimeError(
                f"ASSERTION FAILED: required column {column} has zero populated rows "
                "among serving rows"
            )
        if ratio < threshold:
            raise RuntimeError(
                f"{METRICS_COVERAGE_BELOW_THRESHOLD} column={column} "
                f"populated={populated}/{total} ratio={ratio:.4f} threshold={threshold:.2f}"
            )
