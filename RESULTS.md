# RESULTS

All headline numbers below are `PENDING_RERUN` until measured GKE artifacts exist.

Authority split:
- Locust: request counts, successes, failures
- Prometheus: CPU and timing
- kubectl API: replica counts

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
