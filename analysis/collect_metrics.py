"""
Queries Prometheus HTTP API to collect experiment metrics and export to CSV.

Authority split (documented):
  - Locust is authoritative for request counts, successes, and failures.
  - Prometheus is authoritative for CPU and timing metrics in this module.
  - Replicas are sampled from the Kubernetes API via kubectl (not Prometheus).

Usage:
  python3 analysis/collect_metrics.py \\
    --mode fixed \\
    --prometheus-url http://localhost:9090 \\
    --start 2026-03-17T23:04:57Z \\
    --end 2026-03-17T23:22:57Z \\
    --output results/runs/<run_id>/rep-1/fixed_metrics.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

MISSING = "MISSING"

FIELDNAMES = [
    "timestamp",
    "elapsed_seconds",
    "experiment",
    "data_source",
    "run_id",
    "cluster_name",
    "collection_timestamp",
    "replicas",
    "cpu_utilization_pct",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "rps",
    "error_rate",
]

REQUIRED_VALUE_COLUMNS = [
    "cpu_utilization_pct",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "rps",
    "replicas",
]

DEPLOYMENT_BY_MODE = {
    "fixed": "hpa-eval-fixed",
    "hpa": "hpa-eval-hpa",
}

OPPOSITE_EXPERIMENT = {"fixed": "hpa", "hpa": "fixed"}


def build_queries(mode: str) -> dict[str, str]:
    label = f'experiment="{mode}"'
    return {
        "cpu_utilization_pct": f"avg(app_cpu_usage_percent{{{label}}})",
        "latency_p50_ms": (
            f"histogram_quantile(0.50, sum(rate(app_request_latency_seconds_bucket{{{label}}}[1m])) by (le)) * 1000"
        ),
        "latency_p95_ms": (
            f"histogram_quantile(0.95, sum(rate(app_request_latency_seconds_bucket{{{label}}}[1m])) by (le)) * 1000"
        ),
        "latency_p99_ms": (
            f"histogram_quantile(0.99, sum(rate(app_request_latency_seconds_bucket{{{label}}}[1m])) by (le)) * 1000"
        ),
        "rps": f'sum(rate(app_requests_total{{{label},status_code="200"}}[1m]))',
        "error_rate": (
            f'sum(rate(app_requests_total{{{label},status_code!="200"}}[1m])) / '
            f"sum(rate(app_requests_total{{{label}}}[1m]))"
        ),
    }


def parse_iso8601(value: str) -> float:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).timestamp()


def query_range_raw(
    prometheus_url: str,
    promql: str,
    start: float,
    end: float,
    step: int,
    retries: int = 3,
) -> list[dict]:
    params = urllib.parse.urlencode(
        {
            "query": promql,
            "start": start,
            "end": end,
            "step": f"{step}s",
        }
    )
    url = f"{prometheus_url.rstrip('/')}/api/v1/query_range?{params}"
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                data = json.load(resp)
            if data.get("status") != "success":
                raise RuntimeError(f"prometheus status={data.get('status')}")
            return data.get("data", {}).get("result", [])
        except Exception as exc:  # noqa: BLE001 - abort with named reason
            last_error = exc
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"PROMETHEUS_QUERY_FAILED after {retries} attempts: {last_error}")


def query_range(
    prometheus_url: str,
    promql: str,
    start: float,
    end: float,
    step: int,
    allow_empty: bool = False,
) -> list[tuple[float, float]]:
    results = query_range_raw(prometheus_url, promql, start, end, step)
    if len(results) == 0:
        if allow_empty:
            return []
        label_sets: list[dict] = []
        raise RuntimeError(
            f"AMBIGUOUS_PROMQL_RESULT expected 1 series got 0 labels={label_sets}"
        )
    if len(results) != 1:
        label_sets = [item.get("metric", {}) for item in results]
        raise RuntimeError(
            f"AMBIGUOUS_PROMQL_RESULT expected 1 series got {len(results)} labels={label_sets}"
        )
    values = results[0].get("values", [])
    return [(float(ts), float(val)) for ts, val in values]


def assert_label_isolation(prometheus_url: str, mode: str) -> int:
    own = f'count(app_requests_total{{experiment="{mode}"}})'
    opposite = OPPOSITE_EXPERIMENT[mode]
    other = f'count(app_requests_total{{experiment="{opposite}"}})'

    own_results = query_range_raw(
        prometheus_url, own, time.time() - 120, time.time(), 15, retries=1
    )
    other_results = query_range_raw(
        prometheus_url, other, time.time() - 120, time.time(), 15, retries=1
    )

    if not own_results:
        raise RuntimeError(f"LABEL_ISOLATION_FAILED no series for experiment={mode}")

    own_count = len(own_results)
    other_count = len(other_results)
    if other_count > 0:
        other_labels = [item.get("metric", {}) for item in other_results]
        raise RuntimeError(
            f"LABEL_ISOLATION_FAILED opposite arm present experiment={opposite} series={other_count} labels={other_labels}"
        )

    print(f"LABEL_ISOLATION_VERIFIED experiment={mode} series={own_count}")
    print("OPPOSITE_ARM_SERIES=0")
    return own_count


def sample_ready_replicas(namespace: str, deployment: str) -> int | None:
    cmd = [
        "kubectl",
        "get",
        "deployment",
        deployment,
        "-n",
        namespace,
        "-o",
        "jsonpath={.status.readyReplicas}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    if value == "":
        return 0
    try:
        return int(value)
    except ValueError:
        return None


def collect(
    mode: str,
    prometheus_url: str,
    start_ts: float,
    end_ts: float,
    step: int,
    namespace: str,
    run_id: str,
    cluster_name: str,
    assert_replicas: int | None = None,
    max_replicas: int | None = None,
) -> list[dict]:
    print(f"ANCHOR_WINDOW_ENFORCED start={datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()} end={datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat()}")

    assert_label_isolation(prometheus_url, mode)

    queries = build_queries(mode)
    series: dict[str, list[tuple[float, float]]] = {}
    for metric, promql in queries.items():
        print(f"Querying {metric}: {promql}")
        series[metric] = query_range(
            prometheus_url,
            promql,
            start_ts,
            end_ts,
            step,
            allow_empty=(metric == "error_rate"),
        )

    ref = series.get("cpu_utilization_pct") or next(v for v in series.values() if v)
    deployment = DEPLOYMENT_BY_MODE[mode]
    rows: list[dict] = []

    for ts, _ in ref:
        replica_val = sample_ready_replicas(namespace, deployment)
        if assert_replicas is not None and replica_val is not None and replica_val != assert_replicas:
            msg = (
                f"ASSERTION FAILED: {mode} arm expected {assert_replicas} replicas, observed {replica_val}"
            )
            print(msg, file=sys.stderr)
            raise RuntimeError(msg)
        if max_replicas is not None and replica_val is not None and replica_val > max_replicas:
            raise RuntimeError(
                f"ASSERTION FAILED: {mode} arm replicas {replica_val} exceed maxReplicas {max_replicas}"
            )

        row = {
            "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "elapsed_seconds": int(ts - start_ts),
            "experiment": mode,
            "data_source": "MEASURED",
            "run_id": run_id,
            "cluster_name": cluster_name,
            "collection_timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "replicas": replica_val if replica_val is not None else MISSING,
        }
        for metric, values in series.items():
            val = next((v for t, v in values if abs(t - ts) < step), None)
            row[metric] = round(val, 4) if val is not None else MISSING
        rows.append(row)

    for col in REQUIRED_VALUE_COLUMNS:
        populated = sum(1 for row in rows if row.get(col) not in (MISSING, "", None))
        if populated == 0:
            raise RuntimeError(f"ASSERTION FAILED: required column {col} has zero populated rows")

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Prometheus metrics for HPA evaluation")
    parser.add_argument("--mode", choices=["fixed", "hpa"], required=True)
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument("--start", required=True, help="ISO8601 start timestamp")
    parser.add_argument("--end", required=True, help="ISO8601 end timestamp")
    parser.add_argument("--duration-minutes", type=int, help="Optional helper; does not replace --start/--end")
    parser.add_argument("--step", type=int, default=15)
    parser.add_argument("--namespace", default="hpa-eval")
    parser.add_argument("--run-id", default="local")
    parser.add_argument("--cluster-name", default="local")
    parser.add_argument("--output", required=True)
    parser.add_argument("--assert-replicas", type=int)
    parser.add_argument("--max-replicas", type=int)
    parser.add_argument("--check-label-isolation", action="store_true")
    args = parser.parse_args()

    if not args.start or not args.end:
        print("ERROR: --start and --end are required", file=sys.stderr)
        sys.exit(1)

    start_ts = parse_iso8601(args.start)
    end_ts = parse_iso8601(args.end)
    if end_ts <= start_ts:
        print("ERROR: --end must be after --start", file=sys.stderr)
        sys.exit(1)

    if args.check_label_isolation:
        assert_label_isolation(args.prometheus_url, args.mode)

    rows = collect(
        mode=args.mode,
        prometheus_url=args.prometheus_url,
        start_ts=start_ts,
        end_ts=end_ts,
        step=args.step,
        namespace=args.namespace,
        run_id=args.run_id,
        cluster_name=args.cluster_name,
        assert_replicas=args.assert_replicas,
        max_replicas=args.max_replicas,
    )

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    populated_error = sum(1 for row in rows if row.get("error_rate") not in (MISSING, "", None))
    print(f"FIXED_METRICS_REQUIRED_COLUMNS_POPULATED rows={len(rows)}")
    if populated_error > 0:
        print("ERROR_RATE_COLUMN_POPULATED")
    else:
        print("ERROR_RATE_COLUMN_POPULATED=0 (no non-200 /cpu traffic observed)")

    print(f"Wrote {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
