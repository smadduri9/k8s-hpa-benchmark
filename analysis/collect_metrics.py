"""
Queries Prometheus HTTP API to collect experiment metrics and export to CSV.

Published analysis window (--start/--end): inclusive LOAD_START t0 through t0+RUN_TIME.
Every timestamp in that window is written to CSV; coverage assessment uses all
published rows with no warmup or edge exclusions. Unqueryable cells are MISSING.

Prometheus rate(...[1m]) needs samples before t0; queries use a 60s pre-roll
(METRIC_QUERY_PREROLL_SEC) before --start. Pre-roll rows are not published.

Authority split (documented):
  - Locust is authoritative for request counts, successes, and failures.
  - Prometheus is authoritative for CPU and timing metrics in this module.
  - Replicas are sampled from the Kubernetes API via kubectl (not Prometheus).
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
from pathlib import Path

_ANALYSIS_DIR = Path(__file__).resolve().parent
if str(_ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS_DIR))

from metrics_contract import (
    METRIC_QUERY_PREROLL_SEC,
    MISSING,
    REQUIRED_VALUE_COLUMNS,
    assert_column_coverage,
    column_coverage,
)

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

DEPLOYMENT_BY_MODE = {
    "fixed": "hpa-eval-fixed",
    "hpa": "hpa-eval-hpa",
}

OPPOSITE_EXPERIMENT = {"fixed": "hpa", "hpa": "fixed"}


# Prometheus default scrape interval in k8s/prometheus/configmap.yaml
PROMETHEUS_SCRAPE_INTERVAL_SEC = 15


def rate_window_sec(step: int) -> int:
    """Rate/increase lookback: at least 2x scrape interval, aligned to step."""
    return max(step, 2 * PROMETHEUS_SCRAPE_INTERVAL_SEC)


def build_queries(mode: str, step: int) -> dict[str, str]:
    label = f'experiment="{mode}"'
    rate_window = f"{rate_window_sec(step)}s"
    return {
        "cpu_utilization_pct": f"avg(app_cpu_usage_percent{{{label}}})",
        "latency_p50_ms": (
            f"histogram_quantile(0.50, sum(rate(app_request_latency_seconds_bucket{{{label}}}[{rate_window}])) by (le)) * 1000"
        ),
        "latency_p95_ms": (
            f"histogram_quantile(0.95, sum(rate(app_request_latency_seconds_bucket{{{label}}}[{rate_window}])) by (le)) * 1000"
        ),
        "latency_p99_ms": (
            f"histogram_quantile(0.99, sum(rate(app_request_latency_seconds_bucket{{{label}}}[{rate_window}])) by (le)) * 1000"
        ),
        "rps": f'sum(rate(app_requests_total{{{label},status_code="200"}}[{rate_window}]))',
    }


def build_error_rate_queries(mode: str, step: int) -> dict[str, str]:
    label = f'experiment="{mode}"'
    rate_window = f"{rate_window_sec(step)}s"
    return {
        "request_total_rate": f"sum(rate(app_requests_total{{{label}}}[{rate_window}]))",
        "request_failure_rate": (
            f'sum(rate(app_requests_total{{{label},status_code!="200"}}[{rate_window}]))'
        ),
    }


def compute_error_rate_value(
    total_val: float | None,
    failure_val: float | None,
    failures_series_present: bool,
) -> str | float:
    """failures/total; 0.0 when total>0 and failures=0; MISSING when total unavailable."""
    if total_val is None:
        return MISSING
    if total_val > 0:
        failures = failure_val if failure_val is not None else 0.0
        return round(failures / total_val, 4)
    if failures_series_present and failure_val is not None and failure_val > 0:
        return MISSING
    return 0.0


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


def query_instant(
    prometheus_url: str,
    promql: str,
    ts: float,
    retries: int = 3,
) -> list[dict]:
    params = urllib.parse.urlencode({"query": promql, "time": f"{ts}"})
    url = f"{prometheus_url.rstrip('/')}/api/v1/query?{params}"
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                data = json.load(resp)
            if data.get("status") != "success":
                raise RuntimeError(f"prometheus status={data.get('status')}")
            return data.get("data", {}).get("result", [])
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"PROMETHEUS_QUERY_FAILED after {retries} attempts: {last_error}")


def _instant_scalar(results: list[dict]) -> float:
    if not results:
        return 0.0
    return float(results[0].get("value", [0, "0"])[1])


def assert_label_isolation(
    prometheus_url: str,
    mode: str,
    start_ts: float | None = None,
    end_ts: float | None = None,
) -> int:
    end = end_ts if end_ts is not None else time.time()
    start = start_ts if start_ts is not None else end - 120
    window_sec = max(int(end - start), 15)
    opposite = OPPOSITE_EXPERIMENT[mode]
    own_promql = f'sum(increase(app_requests_total{{experiment="{mode}"}}[{window_sec}s]))'
    other_promql = f'sum(increase(app_requests_total{{experiment="{opposite}"}}[{window_sec}s]))'

    own_results = query_instant(prometheus_url, own_promql, end, retries=1)
    other_results = query_instant(prometheus_url, other_promql, end, retries=1)

    own_increase = _instant_scalar(own_results)
    other_increase = _instant_scalar(other_results)

    if own_increase <= 0:
        raise RuntimeError(
            f"LABEL_ISOLATION_FAILED no request increase for experiment={mode} in {window_sec}s window"
        )

    if other_increase > 0:
        raise RuntimeError(
            f"LABEL_ISOLATION_FAILED opposite arm traffic experiment={opposite} increase={other_increase} in {window_sec}s window"
        )

    print(f"LABEL_ISOLATION_VERIFIED experiment={mode} increase={own_increase}")
    print("OPPOSITE_ARM_SERIES=0")
    return 1


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
    min_replicas: int | None = None,
    run_label_isolation_check: bool = True,
) -> list[dict]:
    publish_start_ts = start_ts
    publish_end_ts = end_ts
    query_start_ts = publish_start_ts - METRIC_QUERY_PREROLL_SEC

    print(
        "ANCHOR_WINDOW_ENFORCED "
        f"start={datetime.fromtimestamp(publish_start_ts, tz=timezone.utc).isoformat()} "
        f"end={datetime.fromtimestamp(publish_end_ts, tz=timezone.utc).isoformat()}"
    )
    print(
        f"METRIC_QUERY_PREROLL_SEC={METRIC_QUERY_PREROLL_SEC} "
        f"rate_window_sec={rate_window_sec(step)} "
        f"query_start={datetime.fromtimestamp(query_start_ts, tz=timezone.utc).isoformat()} "
        "published_rows_only=true no_row_exclusions=true"
    )

    if run_label_isolation_check:
        assert_label_isolation(prometheus_url, mode, publish_start_ts, publish_end_ts)

    deployment = DEPLOYMENT_BY_MODE[mode]
    if mode == "hpa" and min_replicas is not None:
        peak_replicas = 0
        ts = publish_start_ts
        while ts <= publish_end_ts:
            replica_val = sample_ready_replicas(namespace, deployment)
            if replica_val is not None:
                peak_replicas = max(peak_replicas, replica_val)
            ts += step
        if peak_replicas <= min_replicas:
            msg = (
                f"HPA_NEVER_SCALED peak_observed={peak_replicas} minReplicas={min_replicas}"
            )
            print(msg, file=sys.stderr)
            raise RuntimeError(msg)
        print(f"HPA_SCALE_FLOOR_CHECK peak={peak_replicas} minReplicas={min_replicas}")

    queries = build_queries(mode, step)
    error_queries = build_error_rate_queries(mode, step)
    series: dict[str, list[tuple[float, float]]] = {}
    for metric, promql in queries.items():
        print(f"Querying {metric}: {promql}")
        series[metric] = query_range(
            prometheus_url,
            promql,
            query_start_ts,
            publish_end_ts,
            step,
        )

    print(f"Querying error_rate components: {error_queries['request_total_rate']}")
    total_rate_series = query_range(
        prometheus_url,
        error_queries["request_total_rate"],
        query_start_ts,
        publish_end_ts,
        step,
        allow_empty=True,
    )
    print(f"Querying error_rate components: {error_queries['request_failure_rate']}")
    failure_rate_raw = query_range_raw(
        prometheus_url,
        error_queries["request_failure_rate"],
        query_start_ts,
        publish_end_ts,
        step,
        retries=1,
    )
    failures_series_present = len(failure_rate_raw) > 0
    failure_rate_series = (
        [(float(ts), float(val)) for ts, val in failure_rate_raw[0].get("values", [])]
        if failures_series_present
        else []
    )

    ref = series.get("cpu_utilization_pct") or next(v for v in series.values() if v)
    rows: list[dict] = []

    for ts, _ in ref:
        if ts < publish_start_ts - (step / 2) or ts > publish_end_ts + (step / 2):
            continue
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
            "elapsed_seconds": int(ts - publish_start_ts),
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
        total_val = next((v for t, v in total_rate_series if abs(t - ts) < step), None)
        failure_val = next((v for t, v in failure_rate_series if abs(t - ts) < step), None)
        row["error_rate"] = compute_error_rate_value(
            total_val,
            failure_val,
            failures_series_present,
        )
        rows.append(row)

    assert_column_coverage(rows)
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
    parser.add_argument("--min-replicas", type=int)
    parser.add_argument("--check-label-isolation", action="store_true")
    parser.add_argument("--skip-label-isolation", action="store_true")
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
        start_ts = parse_iso8601(args.start)
        end_ts = parse_iso8601(args.end)
        assert_label_isolation(args.prometheus_url, args.mode, start_ts, end_ts)

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
        min_replicas=args.min_replicas,
        run_label_isolation_check=not args.skip_label_isolation,
    )

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    populated_error, total_rows, error_ratio = column_coverage(rows, "error_rate")
    nonzero_error = sum(
        1
        for row in rows
        if row.get("error_rate") not in (MISSING, "", None) and float(row["error_rate"]) > 0
    )
    print(f"FIXED_METRICS_REQUIRED_COLUMNS_POPULATED rows={len(rows)}")
    print(
        f"ERROR_RATE_COLUMN_POPULATED rows={populated_error}/{total_rows} "
        f"non_zero={nonzero_error} missing={total_rows - populated_error}"
    )
    if populated_error == 0:
        raise RuntimeError("ASSERTION FAILED: error_rate column has zero populated rows")

    print(f"Wrote {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
