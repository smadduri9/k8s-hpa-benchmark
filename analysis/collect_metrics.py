"""
Queries Prometheus HTTP API to collect experiment metrics and export to CSV.

Published analysis window (--start/--end): inclusive LOAD_START t0 through t0+RUN_TIME.
Every timestamp in that window is written to CSV. Rows with ready_replicas == 0 are
UNAVAILABLE (metric cells TARGET_UNAVAILABLE). Coverage is over serving rows
(AVAILABLE + DEGRADED: ready_replicas > 0).

Prometheus error_rate counts SERVER-OBSERVED non-200 responses
(app_requests_total{status_code!="200"}). Locust is authoritative for the published
failure rate: client-side connection failures and timeouts ("Unexpected status 0")
never reach the app and do not increment server counters. Example run-20260904T230444Z:
Locust HPA 63/20820 (0.30%) vs Prometheus error_rate non_zero=0; fixed 1230/10193
(12.07%) vs Prometheus error_rate ~0.0 for the same reason.

Prometheus rate(...[1m]) needs samples before t0; queries use a 60s pre-roll
(METRIC_QUERY_PREROLL_SEC) before --start. Pre-roll rows are not published.

Authority split (documented):
  - Locust is authoritative for request counts, successes, and failures.
  - Prometheus is authoritative for CPU and timing metrics in this module.
  - Replicas are sampled from the Kubernetes API during the load window (see
    scripts/lib/replica_sampler.sh) and read from --replica-series at collection.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_ANALYSIS_DIR = Path(__file__).resolve().parent
if str(_ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS_DIR))

from metrics_contract import (
    AVAILABILITY_UNAVAILABLE,
    METRIC_QUERY_PREROLL_SEC,
    METRIC_VALUE_COLUMNS,
    MISSING,
    RATE_DERIVED_COLUMNS,
    REQUIRED_VALUE_COLUMNS,
    TARGET_UNAVAILABLE,
    assert_column_coverage,
    column_coverage_available_rows,
    is_populated_metric_value,
    is_serving_row,
    row_availability_state,
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
    "spec_replicas",
    "status_replicas",
    "ready_replicas",
    "availability_state",
    "cpu_utilization_pct",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "rps",
    "error_rate",
]

OPPOSITE_EXPERIMENT = {"fixed": "hpa", "hpa": "fixed"}

# Whether an HPA arm that never left minReplicas aborts the run. Steady-state load
# shapes expect a calibrated HPA to hold the floor; burst shapes must scale.
HPA_NO_SCALE_ABORT = "abort"
HPA_NO_SCALE_WARN = "warn"

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
    *,
    target_unavailable: bool = False,
) -> str | float:
    """failures/total; 0.0 when total>0 and failures=0; TARGET_UNAVAILABLE when no pods."""
    if target_unavailable:
        return TARGET_UNAVAILABLE
    if total_val is None:
        return MISSING
    if total_val > 0:
        failures = failure_val if failure_val is not None else 0.0
        return round(failures / total_val, 4)
    if failures_series_present and failure_val is not None and failure_val > 0:
        return MISSING
    return 0.0


def anchored_timestamps(publish_start_ts: float, publish_end_ts: float, step: int) -> list[float]:
    """Every step-aligned timestamp from t0 through t0+RUN_TIME inclusive."""
    count = int(round((publish_end_ts - publish_start_ts) / step)) + 1
    return [publish_start_ts + i * step for i in range(count)]


def classify_metric_value(raw_val: float | None, *, target_unavailable: bool) -> str | float:
    if target_unavailable:
        return TARGET_UNAVAILABLE
    if raw_val is None:
        return MISSING
    return round(raw_val, 4)


def lookup_series_value(
    values: list[tuple[float, float]],
    ts: float,
    step: int,
) -> float | None:
    match = next((v for t, v in values if abs(t - ts) < step), None)
    return match


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


@dataclass(frozen=True)
class ReplicaSample:
    ts: float
    spec: int
    status: int
    ready: int


def _parse_replica_int(raw: str, field: str, row: dict, path: Path) -> int:
    value = raw.strip()
    if value == "":
        if field == "ready_replicas":
            return 0
        raise RuntimeError(
            f"REPLICA_SERIES_INVALID path={path} reason=empty_{field} row={row}"
        )
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(
            f"REPLICA_SERIES_INVALID path={path} reason=non_numeric_{field} row={row}"
        ) from exc


def load_replica_series(path: Path) -> list[ReplicaSample]:
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"REPLICA_SERIES_MISSING path={path}")
    samples: list[ReplicaSample] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "timestamp" not in reader.fieldnames:
            raise RuntimeError(f"REPLICA_SERIES_INVALID path={path} reason=missing_timestamp_column")
        required = ("spec_replicas", "status_replicas", "ready_replicas")
        for column in required:
            if column not in reader.fieldnames:
                raise RuntimeError(
                    f"REPLICA_SERIES_INVALID path={path} reason=missing_{column}_column"
                )
        for row in reader:
            ts_raw = row.get("timestamp", "").strip()
            if not ts_raw:
                continue
            try:
                ts = parse_iso8601(ts_raw)
            except ValueError as exc:
                raise RuntimeError(
                    f"REPLICA_SERIES_INVALID path={path} reason=bad_timestamp row={row}"
                ) from exc
            spec = _parse_replica_int(row.get("spec_replicas", ""), "spec_replicas", row, path)
            status = _parse_replica_int(row.get("status_replicas", ""), "status_replicas", row, path)
            ready = _parse_replica_int(row.get("ready_replicas", ""), "ready_replicas", row, path)
            samples.append(ReplicaSample(ts=ts, spec=spec, status=status, ready=ready))
    if not samples:
        raise RuntimeError(f"REPLICA_SERIES_EMPTY path={path}")
    samples.sort(key=lambda item: item.ts)
    return samples


def replica_at_timestamp(
    samples: list[ReplicaSample],
    ts: float,
    step: int,
) -> ReplicaSample | None:
    tolerance = step
    best: ReplicaSample | None = None
    best_delta = tolerance + 1
    for sample in samples:
        delta = abs(sample.ts - ts)
        if delta <= tolerance and delta < best_delta:
            best = sample
            best_delta = delta
    return best


def samples_in_window(
    samples: list[ReplicaSample],
    start_ts: float,
    end_ts: float,
) -> list[ReplicaSample]:
    return [sample for sample in samples if start_ts <= sample.ts <= end_ts]


def evaluate_replica_series(
    mode: str,
    samples: list[ReplicaSample],
    publish_start_ts: float,
    publish_end_ts: float,
    assert_replicas: int | None = None,
    min_replicas: int | None = None,
    hpa_no_scale_policy: str = HPA_NO_SCALE_ABORT,
) -> tuple[int, int, int, int]:
    window_samples = samples_in_window(samples, publish_start_ts, publish_end_ts)
    if not window_samples:
        raise RuntimeError(
            "REPLICA_SERIES_EMPTY "
            f"window_start={datetime.fromtimestamp(publish_start_ts, tz=timezone.utc).isoformat()} "
            f"window_end={datetime.fromtimestamp(publish_end_ts, tz=timezone.utc).isoformat()}"
        )

    peak_spec = max(sample.spec for sample in window_samples)
    peak_ready = max(sample.ready for sample in window_samples)
    min_ready = min(sample.ready for sample in window_samples)

    if mode == "fixed" and assert_replicas is not None:
        if peak_ready < assert_replicas:
            msg = (
                f"REPLICA_BELOW_DECLARED peak_observed={peak_ready} declared={assert_replicas}"
            )
            print(msg, file=sys.stderr)
            raise RuntimeError(msg)
        if min_ready < assert_replicas:
            dip_times = [
                datetime.fromtimestamp(sample.ts, tz=timezone.utc).isoformat()
                for sample in window_samples
                if sample.ready < assert_replicas
            ]
            print(
                "REPLICA_DIP_OBSERVED "
                f"declared={assert_replicas} minimum={min_ready} "
                f"dip_timestamps={','.join(dip_times)}"
            )

    if mode == "hpa" and min_replicas is not None:
        if peak_spec <= min_replicas:
            if hpa_no_scale_policy == HPA_NO_SCALE_ABORT:
                msg = (
                    f"HPA_NEVER_SCALED peak_observed={peak_spec} minReplicas={min_replicas}"
                )
                print(msg, file=sys.stderr)
                raise RuntimeError(msg)
            # Steady-load shapes: a calibrated HPA holding the floor is the
            # measurement, not a harness failure. Aborting would discard the result.
            print(
                f"HPA_DID_NOT_SCALE_ON_STEADY_LOAD peak_spec={peak_spec} "
                f"minReplicas={min_replicas} policy={hpa_no_scale_policy}"
            )
        print(
            f"HPA_SCALE_FLOOR_CHECK peak={peak_spec} minReplicas={min_replicas} "
            f"peak_ready={peak_ready}"
        )

    return peak_spec, peak_ready, min_ready


def collect(
    mode: str,
    prometheus_url: str,
    start_ts: float,
    end_ts: float,
    step: int,
    namespace: str,
    run_id: str,
    cluster_name: str,
    replica_series_path: Path,
    assert_replicas: int | None = None,
    max_replicas: int | None = None,
    min_replicas: int | None = None,
    run_label_isolation_check: bool = True,
    hpa_no_scale_policy: str = HPA_NO_SCALE_ABORT,
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

    replica_samples = load_replica_series(replica_series_path)
    print(f"REPLICA_SERIES_LOADED path={replica_series_path} samples={len(replica_samples)}")
    evaluate_replica_series(
        mode,
        replica_samples,
        publish_start_ts,
        publish_end_ts,
        assert_replicas=assert_replicas,
        min_replicas=min_replicas,
        hpa_no_scale_policy=hpa_no_scale_policy,
    )

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
            allow_empty=True,
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

    ref = series.get("cpu_utilization_pct") or next((v for v in series.values() if v), [])
    published_ts = anchored_timestamps(publish_start_ts, publish_end_ts, step)
    declared_for_availability = assert_replicas if mode == "fixed" else None
    if ref:
        ref_ts = {ts for ts, _ in ref}
        missing_ts = [
            ts
            for ts in published_ts
            if not any(abs(existing - ts) < (step / 2) for existing in ref_ts)
        ]
        if missing_ts:
            print(
                "PROMETHEUS_SERIES_GAP "
                f"expected_rows={len(published_ts)} prom_ref_rows={len(ref)} "
                f"gap_rows={len(missing_ts)} anchor_fill=true"
            )

    rows: list[dict] = []

    for ts in published_ts:
        sample = replica_at_timestamp(replica_samples, ts, step)
        if sample is None:
            raise RuntimeError(
                "REPLICA_SERIES_GAP "
                f"timestamp={datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()} "
                f"path={replica_series_path}"
            )
        availability_state = row_availability_state(
            {"ready_replicas": sample.ready},
            declared_replicas=declared_for_availability,
        )
        target_unavailable = availability_state == AVAILABILITY_UNAVAILABLE
        if (
            max_replicas is not None
            and sample.spec > max_replicas
        ):
            raise RuntimeError(
                f"ASSERTION FAILED: {mode} arm spec_replicas {sample.spec} exceed maxReplicas {max_replicas}"
            )

        row = {
            "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "elapsed_seconds": int(ts - publish_start_ts),
            "experiment": mode,
            "data_source": "MEASURED",
            "run_id": run_id,
            "cluster_name": cluster_name,
            "collection_timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "replicas": sample.ready,
            "spec_replicas": sample.spec,
            "status_replicas": sample.status,
            "ready_replicas": sample.ready,
            "availability_state": availability_state,
        }
        for metric in METRIC_VALUE_COLUMNS:
            if metric == "error_rate":
                continue
            raw_val = lookup_series_value(series.get(metric, []), ts, step)
            row[metric] = classify_metric_value(
                raw_val,
                target_unavailable=target_unavailable,
            )
        total_val = lookup_series_value(total_rate_series, ts, step)
        failure_val = lookup_series_value(failure_rate_series, ts, step)
        row["error_rate"] = compute_error_rate_value(
            total_val,
            failure_val,
            failures_series_present,
            target_unavailable=target_unavailable,
        )
        rows.append(row)

    assert_column_coverage(rows, declared_replicas=declared_for_availability)
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
    parser.add_argument("--replica-series", required=True, help="CSV of in-run kubectl replica samples")
    parser.add_argument("--assert-replicas", type=int)
    parser.add_argument("--max-replicas", type=int)
    parser.add_argument("--min-replicas", type=int)
    parser.add_argument("--check-label-isolation", action="store_true")
    parser.add_argument("--skip-label-isolation", action="store_true")
    parser.add_argument(
        "--hpa-no-scale-policy",
        choices=[HPA_NO_SCALE_ABORT, HPA_NO_SCALE_WARN],
        default=HPA_NO_SCALE_ABORT,
        help=(
            "hpa arm never leaving minReplicas: abort (default; burst shapes must scale) "
            "or warn (steady-state shapes, where holding the floor is the result)"
        ),
    )
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
        replica_series_path=Path(args.replica_series),
        assert_replicas=args.assert_replicas,
        max_replicas=args.max_replicas,
        min_replicas=args.min_replicas,
        run_label_isolation_check=not args.skip_label_isolation,
        hpa_no_scale_policy=args.hpa_no_scale_policy,
    )

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    populated_error, total_available, _error_ratio = column_coverage_available_rows(
        rows, "error_rate", declared_replicas=args.assert_replicas if args.mode == "fixed" else None
    )
    unavailable_rows = sum(
        1 for row in rows if row.get("availability_state") == AVAILABILITY_UNAVAILABLE
    )
    nonzero_error = sum(
        1
        for row in rows
        if is_serving_row(row)
        and is_populated_metric_value(row.get("error_rate"))
        and float(row["error_rate"]) > 0
    )
    print(f"FIXED_METRICS_REQUIRED_COLUMNS_POPULATED rows={len(rows)}")
    print(
        f"ERROR_RATE_COLUMN_POPULATED rows={populated_error}/{total_available} "
        f"non_zero={nonzero_error} missing={total_available - populated_error} "
        f"target_unavailable_rows={unavailable_rows}"
    )
    if total_available == 0:
        raise RuntimeError("ASSERTION FAILED: zero available rows for error_rate assessment")

    print(f"Wrote {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
