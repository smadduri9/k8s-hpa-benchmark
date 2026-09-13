# Phase 5 — histogram bucket columns for server-side `/cpu` SLI

**Implemented in the current collector.** The seven columns below are in `analysis/collect_metrics.py` and registered in `analysis/metrics_contract.py`. Earlier CSVs without them cannot recover counts from quantiles. The v1.1 bundle omits Prometheus CSVs and publishes no exact SLI or metrics-coverage result.

These are Prometheus `increase()` estimates over lookback windows, not integer raw counters. Successive 30-second windows sampled every 15 seconds overlap; summing rows double-counts observations. They measure handler duration, not the client-observed SLO. Run-level aggregation with explicit boundaries and separate client instrumentation would be required for those claims.

## Why this is needed

Quantiles cannot be inverted into counts. Bucket increases support an estimated fraction of handler observations at or below a threshold in each lookback window. Phase 2 used approximate Locust percentile brackets, a different measurement scope.

## Scope

- **Metric:** `app_request_latency_seconds_bucket` (`app/main.py`; only `endpoint="/cpu"` is observed).
- **Arms:** `experiment="fixed"` and `experiment="hpa"` — same label selector pattern as existing latency queries in `collect_metrics.py`.
- **`GET /`:** not instrumented; excluded from SLI (unchanged).

## `cpu_utilization_pct` (existing column)

| Field | Definition |
|-------|------------|
| **PromQL** | `avg(app_cpu_usage_percent{experiment="{mode}"})` |
| **App gauge** | `psutil.cpu_percent(interval=None)` with no process argument — **system-wide CPU** |
| **Container scope** | Without lxcfs (GKE does not mount it), `/proc/stat` inside the pod reflects **node-level** CPU, not cgroup/pod CPU |
| **HPA metric** | **Not** this column. HPA uses metrics-server utilisation against the **500m request** |
| **Published CSVs** | Values are real **node utilisation** samples; the column name implies pod CPU — read accordingly. Do not delete or recompute existing cells |

The P1 capacity probe (`scripts/run_capacity_probe.sh`) uses **metrics-server** (`kubectl top pods`) for pod-scoped millicores. That source is separate from `cpu_utilization_pct`.

## Rate window

Use the same lookback as other rate-derived columns in `collect_metrics.py`:

- `rate_window_sec(step) = max(step, 2 * PROMETHEUS_SCRAPE_INTERVAL_SEC)` (15 s scrape → minimum **30 s** when `step` is smaller).
- PromQL window: `[{rate_window}s]` where `{rate_window}` is that value in seconds (example: `[30s]`).

Do **not** use `histogram_quantile` for these columns.

## Frozen column names and PromQL

Each column stores the **increase** in the cumulative bucket counter over the rate window ending at the row timestamp. `{mode}` is `fixed` or `hpa`. `{rate_window}` is the seconds literal from the previous section.

| Column name | `le` (seconds) | PromQL |
|-------------|----------------|--------|
| `latency_le_100ms_count` | `0.1` | `sum(increase(app_request_latency_seconds_bucket{experiment="{mode}",le="0.1"}[{rate_window}]))` |
| `latency_le_250ms_count` | `0.25` | `sum(increase(app_request_latency_seconds_bucket{experiment="{mode}",le="0.25"}[{rate_window}]))` |
| `latency_le_500ms_count` | `0.5` | `sum(increase(app_request_latency_seconds_bucket{experiment="{mode}",le="0.5"}[{rate_window}]))` |
| `latency_le_1000ms_count` | `1.0` | `sum(increase(app_request_latency_seconds_bucket{experiment="{mode}",le="1.0"}[{rate_window}]))` |
| `latency_le_2500ms_count` | `2.5` | `sum(increase(app_request_latency_seconds_bucket{experiment="{mode}",le="2.5"}[{rate_window}]))` |
| `latency_le_5000ms_count` | `5.0` | `sum(increase(app_request_latency_seconds_bucket{experiment="{mode}",le="5.0"}[{rate_window}]))` |
| `latency_le_inf_count` | `+Inf` | `sum(increase(app_request_latency_seconds_bucket{experiment="{mode}",le="+Inf"}[{rate_window}]))` |

**Denominator:** `latency_le_inf_count` (`le="+Inf"`) is the total `/cpu` request count in the window. All other bucket columns are numerators at or below the stated threshold.

**Server-side threshold (500 ms):** `latency_le_500ms_count` / `latency_le_inf_count` estimates the fraction at or below 500 ms when both are populated and the denominator is positive. This is not a client SLO measurement; an inclusive bucket also differs from a strict faster-than threshold.

**General SLI at threshold T (seconds):** use the column whose `le` equals T (for example `le="0.5"` → `latency_le_500ms_count`) divided by `latency_le_inf_count`.

The seven bucket columns follow `latency_p99_ms` and precede `rps` in `FIELDNAMES`.

## Coverage and availability (no new rules)

Bucket columns are **rate-derived**. They inherit the existing contract in `analysis/metrics_contract.py` — **do not invent a new exclusion category.**

1. **Serving rows only.** Coverage is assessed over rows with `ready_replicas > 0` (`AVAILABLE` and `DEGRADED`). Rows with `ready_replicas == 0` are `UNAVAILABLE`; bucket cells on those rows are `TARGET_UNAVAILABLE`, same as `rps` and `error_rate`.

2. **Burst-onset MISSING.** The first **`BURST_ONSET_RATE_ROW_EXCLUSIONS`** serving rows (**2**) are excluded from rate-column coverage, same as `rps`, `error_rate`, and `latency_p50_ms` / `p95` / `p99`. The seven bucket columns are in `RATE_DERIVED_COLUMNS`.

3. **Coverage gate.** `METRICS_COLUMN_COVERAGE` uses `MIN_COLUMN_COVERAGE_RATIO` (**0.95**) over the eligible serving rows per column, identical to other required rate-derived columns.

4. **Missing data.** When Prometheus returns no sample, write `MISSING` (not `0`).

## Implementation checklist (Phase 5)

- [x] Columns in `FIELDNAMES` in `analysis/collect_metrics.py`
- [x] Queries in `build_queries()` using the PromQL above
- [x] Columns registered in `RATE_DERIVED_COLUMNS` and `METRIC_VALUE_COLUMNS`
- [x] GKE Prometheus PVC manifests (`k8s/prometheus/pvc.yaml`, `deployment-gke.yaml`)
- [ ] Run-level SLI analysis with explicit non-overlapping boundaries; do not sum overlapping row increases

## Related documents

- [`RESULTS.md`](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/RESULTS.md#measurement-limitations) — current publication limitations
- [`docs/error-budget-policy.md`](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/docs/error-budget-policy.md) — proposed client SLO and missing compliance series
