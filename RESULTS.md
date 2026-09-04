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
