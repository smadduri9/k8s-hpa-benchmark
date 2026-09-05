"""
Ingest Locust CSV stats as authoritative request/success/failure evidence.

Authority split:
  - Locust is authoritative for request counts, successes, failures, and
    client-observed response time (run-level percentiles from stats CSV).
  - Prometheus is authoritative for replicas, CPU, and in-handler service time.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

MISSING = "MISSING"

PERCENTILE_COLUMNS = [
    "50%",
    "66%",
    "75%",
    "80%",
    "90%",
    "95%",
    "98%",
    "99%",
    "99.9%",
    "99.99%",
    "100%",
]

TIMING_COLUMNS = [
    "Average Response Time",
    "Min Response Time",
    "Max Response Time",
    "Median Response Time",
]

# Locust stats CSV percentile grid (column headers) for monotonicity brackets.
PERCENTILE_GRID = [0.50, 0.66, 0.75, 0.80, 0.90, 0.95, 0.98, 0.99, 0.999, 0.9999, 1.0]

SUCCESS_ONLY_REASON = (
    "locust StatsEntry logs response times for failed requests via "
    "runners.py on_request; log_error records no timing (stats.py:412)"
)


def _cell_value(raw: str | None) -> str | float:
    if raw is None or raw == "":
        return MISSING
    try:
        return float(raw)
    except ValueError:
        return raw


def _parse_row(row: dict) -> dict:
    parsed: dict = {
        "type": row.get("Type", ""),
        "name": row.get("Name", ""),
        "request_count": int(float(row.get("Request Count", 0))),
        "failure_count": int(float(row.get("Failure Count", 0))),
    }
    for col in TIMING_COLUMNS:
        parsed[col] = _cell_value(row.get(col))
    for col in PERCENTILE_COLUMNS:
        parsed[col] = _cell_value(row.get(col))
    return parsed


def assert_endpoint_counts_match(arm: str, rows: list[dict], aggregated: dict) -> None:
    endpoints = [r for r in rows if r["name"] != "Aggregated"]
    if not endpoints:
        print(
            f"LOCUST_ENDPOINT_COUNT_MISMATCH arm={arm} reason=no_per_endpoint_rows",
            file=sys.stderr,
        )
        sys.exit(1)

    sum_requests = sum(r["request_count"] for r in endpoints)
    sum_failures = sum(r["failure_count"] for r in endpoints)
    agg_requests = aggregated["request_count"]
    agg_failures = aggregated["failure_count"]

    if sum_requests != agg_requests or sum_failures != agg_failures:
        print(
            "LOCUST_ENDPOINT_COUNT_MISMATCH "
            f"arm={arm} "
            f"endpoint_request_sum={sum_requests} aggregated_requests={agg_requests} "
            f"endpoint_failure_sum={sum_failures} aggregated_failures={agg_failures}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        "LOCUST_ENDPOINT_COUNT_ASSERT_PASS "
        f"arm={arm} endpoints={len(endpoints)} "
        f"sum={sum_requests} aggregated={agg_requests}"
    )


def _percentile_column(p: float) -> str:
  if p >= 1.0:
      return "100%"
  if p >= 0.9999:
      return "99.99%"
  if p >= 0.999:
      return "99.9%"
  return f"{int(p * 100)}%"


def _value_bracket_for_bound(
    aggregated: dict, lower_p: float, upper_p: float
) -> dict:
    """Bracket success-only p95 by monotonicity on the published percentile grid only."""
    lower_col = None
    for p in PERCENTILE_GRID:
        col = _percentile_column(p)
        if p <= lower_p:
            lower_col = col
    upper_col = None
    for p in PERCENTILE_GRID:
        col = _percentile_column(p)
        if p >= upper_p:
            upper_col = col
            break

    lower_val = aggregated.get(lower_col, MISSING) if lower_col else MISSING
    upper_val = aggregated.get(upper_col, MISSING) if upper_col else MISSING
    return {
        "lower_percentile_column": lower_col,
        "lower_bound_ms": lower_val,
        "upper_percentile_column": upper_col,
        "upper_bound_ms": upper_val,
    }


def compute_p95_success_bounds(aggregated: dict) -> dict:
    request_count = aggregated["request_count"]
    failure_count = aggregated["failure_count"]
    if request_count == 0:
        return {
            "failure_fraction": MISSING,
            "p95_success_bound_lower_percentile": MISSING,
            "p95_success_bound_upper_percentile": MISSING,
            "p95_success_value_bracket": {},
        }

    f = failure_count / request_count
    lower_p = 0.95 * (1.0 - f)
    upper_p = f + 0.95 * (1.0 - f)
    bracket = _value_bracket_for_bound(aggregated, lower_p, upper_p)
    return {
        "failure_fraction": f,
        "p95_success_bound_lower_percentile": lower_p,
        "p95_success_bound_upper_percentile": upper_p,
        "p95_success_value_bracket": bracket,
    }


def parse_locust_stats(path: str, arm: str) -> dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    rows: list[dict] = []
    aggregated: dict | None = None
    endpoints: dict[str, dict] = {}

    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            parsed = _parse_row(row)
            rows.append(parsed)
            if parsed["name"] == "Aggregated":
                aggregated = parsed
            else:
                key = f"{parsed['type']} {parsed['name']}"
                endpoints[key] = parsed

    if aggregated is None:
        raise ValueError(f"No Aggregated row found in {path}")

    assert_endpoint_counts_match(arm, rows, aggregated)

    request_count = aggregated["request_count"]
    failure_count = aggregated["failure_count"]
    success_count = request_count - failure_count
    failure_rate = (failure_count / request_count) if request_count else 0.0

    bounds = compute_p95_success_bounds(aggregated)

    return {
        "source": path,
        "request_count": request_count,
        "failure_count": failure_count,
        "success_count": success_count,
        "failure_rate": failure_rate,
        "aggregated": aggregated,
        "endpoints": endpoints,
        "percentiles_include_failures": True,
        "percentile_source": "locust_stats_csv",
        "success_only_percentiles_available": False,
        "success_only_reason": SUCCESS_ONLY_REASON,
        **bounds,
    }


def ingest(arm: str, stats_path: str) -> dict:
    stats = parse_locust_stats(stats_path, arm)
    stats["arm"] = arm
    stats["authority"] = "LOCUST"
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Locust stats for both arms")
    parser.add_argument("--fixed-stats", required=True)
    parser.add_argument("--hpa-stats", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if not os.path.isfile(args.fixed_stats):
        print("ASSERTION FAILED: locust_fixed_stats.csv is absent", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(args.hpa_stats):
        print("ASSERTION FAILED: locust_hpa_stats.csv is absent", file=sys.stderr)
        sys.exit(1)

    fixed = ingest("fixed", args.fixed_stats)
    hpa = ingest("hpa", args.hpa_stats)

    payload = {
        "REQUEST_AUTHORITY": "LOCUST",
        "LATENCY_AUTHORITY": "LOCUST_CLIENT_OBSERVED",
        "PROM_AUTHORITY": "REPLICAS_CPU_TIMING",
        "fixed": fixed,
        "hpa": hpa,
    }

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    print("LOCUST_FIXED_STATS_FOUND")
    print("LOCUST_HPA_STATS_FOUND")
    print("REQUEST_AUTHORITY=LOCUST")
    print("LATENCY_AUTHORITY=LOCUST_CLIENT_OBSERVED")
    print("PROM_AUTHORITY=REPLICAS_CPU_TIMING")


if __name__ == "__main__":
    main()
