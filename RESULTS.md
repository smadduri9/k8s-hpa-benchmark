# RESULTS

All headline numbers below are `PENDING_RERUN` until measured GKE artifacts exist.

Authority split:
- Locust: request counts, successes, failures
- Prometheus: CPU and timing
- kubectl API: replica counts

## Prometheus analysis window (published CSV rows)

- **Window:** `LOAD_START t0` through `t0 + RUN_TIME` per arm (inclusive), as recorded in each run's `manifest.json` and echoed as `ANCHOR_WINDOW_ENFORCED start=… end=…` during collection.
- **Burst included:** The first minute of load is in the published window. A 60s scrape pre-roll during the post-cold-start ready wait (`METRIC_SCRAPE_PREROLL_*` in `rep.log`) warms Prometheus scrapes before `t0`; pre-roll samples are queried but not written as CSV rows.
- **No row exclusions:** Coverage assessment (`MIN_COLUMN_COVERAGE_RATIO=0.95`) runs over every published row. Genuinely unqueryable cells are `MISSING` and count against the threshold.
- **Rate lookback:** PromQL `rate(...[N])` uses `N = max(step, 2×scrape_interval)` (30s at default 15s step/scrape) so range queries have enough samples; burst-onset rows are published and assessed like any other row.
- **Log markers:** `METRIC_QUERY_PREROLL_SEC=60 published_rows_only=true no_row_exclusions=true`

### Burst-onset MISSING rows (rate-derived columns)

Two published rows per arm at the start of the window (`t0` and `t0+step`) are often `MISSING` for `latency_p50_ms`, `latency_p95_ms`, `latency_p99_ms`, `rps`, and `error_rate`. This is inherent to Prometheus counter semantics, not a collection failure: `app_requests_total` has no series until the first request arrives, so `rate(...[30s])` has no prior sample to difference against at the exact burst onset.

- **Gauges are complete from t0:** `replicas` and `cpu_utilization_pct` scrape idle pods during pre-roll and are populated for every published row (e.g. 41/41 at 10m smoke, 73/73 at 18m production). HPA scaling behavior is captured from the first row.
- **Symmetric across arms:** Both fixed and HPA arms see the same two-row gap, so comparative analysis is unaffected.
- **Expected coverage (no warm-up traffic):** At `step=15` and `rate_window_sec=30`, expect two `MISSING` rate-derived rows per arm. Published row counts: **41 at 10m smoke** → **39/41 (0.9512)**; **73 at 18m production** → **~71/73 (0.973)**. Do not add pre-burst request traffic to populate these rows — that would alter the counter baseline before `t0`.

## Failure rate (fixed arm)
- **Value:** `PENDING_RERUN`
- **Formula:** `failures / requests` from Locust Aggregated row
- **Source:** `results/runs/<run_id>/rep-N/locust_fixed_stats.csv`, columns `Failure Count`, `Request Count`

## Failure rate (HPA arm)
- **Value:** `PENDING_RERUN`
- **Formula:** `failures / requests` from Locust Aggregated row
- **Source:** `results/runs/<run_id>/rep-N/locust_hpa_stats.csv`, columns `Failure Count`, `Request Count`

## Throughput ratio (HPA / fixed)
- **Value:** `PENDING_RERUN`
- **Formula:** `hpa_successful_requests / fixed_successful_requests`
- **Source:** Locust stats for both arms (not Prometheus RPS)

## Cost per 1k successful requests
- **Value:** `PENDING_RERUN`
- **Formula:** `(pod_hours * list_price_per_pod_hour) / (successful_requests / 1000)`
- **Source:** Tier 2 cost module + Locust successful request counts

## SLO burn (14.4x tier, 1h/5m)
- **Value:** `PENDING_RERUN` (Tier 3 module)
- **Note:** 6h/30m and 3d/6h tiers are `MISSING` by design for 18-minute runs.

## Run-scoped error budget consumption (%)
- **Value:** `PENDING_RERUN`
- **Formula:** `(observed_error_rate / slo_target) * 100` over full run window
- **Source:** Locust failure evidence per arm
