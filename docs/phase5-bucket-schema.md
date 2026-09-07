# Phase 5 — histogram bucket columns for exact `/cpu` SLI

**NOT IMPLEMENTED.** This document is a **specification for Phase 5**. The collector (`analysis/collect_metrics.py`) does **not** write these columns today. Existing metrics CSVs contain `histogram_quantile` results only (`latency_p50_ms`, `latency_p95_ms`, `latency_p99_ms`). **`histogram_quantile` cannot be inverted into counts**, so the exact SLI was not computable retroactively for any existing run.

## Why this is needed

Prometheus-derived latency in published CSVs is a quantile estimate per row. A count-based SLI (for example **99% faster than 500 ms** on `/cpu`) requires cumulative histogram bucket counts. Phase 2 used approximate Locust percentile brackets instead; Phase 5 adds the bucket columns below so the SLI can be computed exactly from Prometheus at collection time.

## Scope

- **Metric:** `app_request_latency_seconds_bucket` (`app/main.py`; only `endpoint="/cpu"` is observed).
- **Arms:** `experiment="fixed"` and `experiment="hpa"` — same label selector pattern as existing latency queries in `collect_metrics.py`.
- **`GET /`:** not instrumented; excluded from SLI (unchanged).

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

**Headline SLO threshold (500 ms):** use `latency_le_500ms_count` / `latency_le_inf_count` when both are populated.

**General SLI at threshold T (seconds):** use the column whose `le` equals T (for example `le="0.5"` → `latency_le_500ms_count`) divided by `latency_le_inf_count`.

Column order in `FIELDNAMES` (after implementation): insert the seven bucket columns immediately after `latency_p99_ms` and before `rps`, preserving existing columns otherwise.

## Coverage and availability (no new rules)

Bucket columns are **rate-derived**. They inherit the existing contract in `analysis/metrics_contract.py` — **do not invent a new exclusion category.**

1. **Serving rows only.** Coverage is assessed over rows with `ready_replicas > 0` (`AVAILABLE` and `DEGRADED`). Rows with `ready_replicas == 0` are `UNAVAILABLE`; bucket cells on those rows are `TARGET_UNAVAILABLE`, same as `rps` and `error_rate`.

2. **Burst-onset MISSING.** The first **`BURST_ONSET_RATE_ROW_EXCLUSIONS`** serving rows (**2**) are excluded from rate-column coverage, same as `rps`, `error_rate`, and `latency_p50_ms` / `p95` / `p99`. Add the seven bucket column names to `RATE_DERIVED_COLUMNS` when implemented.

3. **Coverage gate.** `METRICS_COLUMN_COVERAGE` uses `MIN_COLUMN_COVERAGE_RATIO` (**0.95**) over the eligible serving rows per column, identical to other required rate-derived columns.

4. **Missing data.** When Prometheus returns no sample, write `MISSING` (not `0`).

## Implementation checklist (Phase 5)

- [ ] Add columns to `FIELDNAMES` in `analysis/collect_metrics.py`
- [ ] Add queries to `build_queries()` (or a sibling builder) using the PromQL above
- [ ] Register columns in `RATE_DERIVED_COLUMNS` and `METRIC_VALUE_COLUMNS` in `metrics_contract.py`
- [ ] Add a Prometheus **PersistentVolumeClaim** before long Phase 5 runs (see [`POSTMORTEM.md`](../POSTMORTEM.md))
- [ ] Analysis module to aggregate row increases into run-level exact SLI (separate from this schema doc)

## Related documents

- [`RESULTS.md`](../RESULTS.md#slo-and-error-budget-calibrated-runs) — approximate SLI on existing runs
- [`docs/error-budget-policy.md`](error-budget-policy.md) — SLO target and deferred meet-vs-revise decision
