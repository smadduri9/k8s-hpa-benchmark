"""
Queries Prometheus HTTP API to collect experiment metrics and export to CSV.

Usage:
  python3 analysis/collect_metrics.py --mode fixed --prometheus-url http://localhost:9090
  python3 analysis/collect_metrics.py --mode hpa   --prometheus-url http://localhost:9090

Requires port-forwarding Prometheus before running:
  kubectl port-forward svc/prometheus 9090:9090 -n hpa-eval
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone

import urllib.request
import urllib.parse
import json

# ---------------------------------------------------------------------------
# Prometheus query definitions
# ---------------------------------------------------------------------------

# PromQL expressions keyed by metric name
QUERIES = {
    "cpu_utilization_pct": (
        'avg(app_cpu_usage_percent)'
    ),
    "replicas": (
        'count(count by (instance)(app_requests_total))'
    ),
    "latency_p50_ms": (
        'histogram_quantile(0.50, sum(rate(app_request_latency_seconds_bucket[1m])) by (le)) * 1000'
    ),
    "latency_p95_ms": (
        'histogram_quantile(0.95, sum(rate(app_request_latency_seconds_bucket[1m])) by (le)) * 1000'
    ),
    "latency_p99_ms": (
        'histogram_quantile(0.99, sum(rate(app_request_latency_seconds_bucket[1m])) by (le)) * 1000'
    ),
    "rps": (
        'sum(rate(app_requests_total{status_code="200"}[1m]))'
    ),
    "error_rate": (
        'sum(rate(app_requests_total{status_code!="200"}[1m])) / '
        'sum(rate(app_requests_total[1m]))'
    ),
}

FIELDNAMES = [
    "timestamp", "elapsed_seconds", "experiment",
    "replicas", "cpu_utilization_pct",
    "latency_p50_ms", "latency_p95_ms", "latency_p99_ms",
    "rps", "error_rate",
]


# ---------------------------------------------------------------------------
# Prometheus HTTP client
# ---------------------------------------------------------------------------

def query_range(prometheus_url: str, promql: str, start: float, end: float, step: int = 15):
    """Query Prometheus /api/v1/query_range and return list of (timestamp, value) tuples."""
    params = urllib.parse.urlencode({
        "query": promql,
        "start": start,
        "end": end,
        "step": f"{step}s",
    })
    url = f"{prometheus_url.rstrip('/')}/api/v1/query_range?{params}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.load(resp)
    except Exception as exc:
        print(f"  [WARN] Prometheus query failed: {exc}", file=sys.stderr)
        return []

    if data.get("status") != "success":
        print(f"  [WARN] Prometheus returned status={data.get('status')}", file=sys.stderr)
        return []

    results = data.get("data", {}).get("result", [])
    if not results:
        return []

    # Return first series values
    return [(float(ts), float(val)) for ts, val in results[0].get("values", [])]


# ---------------------------------------------------------------------------
# Main collection loop
# ---------------------------------------------------------------------------

def collect(mode: str, prometheus_url: str, duration_minutes: int = 18, step: int = 15):
    print(f"Collecting metrics for experiment={mode} from {prometheus_url}")
    print(f"  Duration: {duration_minutes} minutes, step: {step}s")

    end_time = time.time()
    start_time = end_time - duration_minutes * 60

    # Query each metric
    series: dict[str, list[tuple[float, float]]] = {}
    for metric, promql in QUERIES.items():
        print(f"  Querying: {metric} ...")
        values = query_range(prometheus_url, promql, start_time, end_time, step)
        series[metric] = values
        print(f"    -> {len(values)} data points")

    if not any(series.values()):
        print("ERROR: No data returned from Prometheus. Is port-forwarding active?", file=sys.stderr)
        sys.exit(1)

    # Build rows aligned by timestamp
    # Use cpu_utilization_pct timestamps as reference
    ref_series = series.get("cpu_utilization_pct") or next(v for v in series.values() if v)
    rows = []
    for ts, _ in ref_series:
        row = {
            "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "elapsed_seconds": int(ts - start_time),
            "experiment": mode,
        }
        for metric, values in series.items():
            # Find closest value within step window
            val = next((v for t, v in values if abs(t - ts) < step), None)
            row[metric] = round(val, 4) if val is not None else ""
        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Collect Prometheus metrics for HPA evaluation")
    parser.add_argument("--mode", choices=["fixed", "hpa"], required=True)
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument("--duration-minutes", type=int, default=18)
    parser.add_argument("--step", type=int, default=15, help="Scrape step in seconds")
    args = parser.parse_args()

    rows = collect(args.mode, args.prometheus_url, args.duration_minutes, args.step)

    output_dir = os.path.join(os.path.dirname(__file__), "..", "sample_data")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{args.mode}_metrics.csv")

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {output_path}")


if __name__ == "__main__":
    main()
