# RESULTS

Authority split:
- **Locust:** request counts, successes, failures (published failure rate)
- **Prometheus:** CPU and timing (server-side observations only)
- **kubectl in-run sampling:** `spec_replicas`, `status_replicas`, `ready_replicas` in `replica_series_<arm>.csv` and metrics CSV

## Headline — run-20260904T230444Z (production, GKE)

The fixed 3-replica baseline **collapsed under burst**: `REPLICA_DIP_OBSERVED declared=3 minimum=0` across 51 timestamps in `replica_series_fixed.csv` — zero ready replicas for a measurable fraction of the 18-minute window. With no pods serving, Prometheus app metrics are absent; those rows are **`UNAVAILABLE`**, not collection failure. Evidence is in `replica_series_fixed.csv`, independent of Prometheus.

| Arm | Locust requests | Locust failures | Failure rate | HPA / replica outcome |
|-----|----------------:|----------------:|-------------:|------------------------|
| **Fixed** | 10,193 | 1,230 | **12.07%** | Declared 3 replicas; ready hit **0** during collapse |
| **HPA** | 20,820 | 63 | **0.30%** | Scaled to **spec=10** (`HPA_SCALE_FLOOR_CHECK peak=10`); ready tracked to 10 |

Availability gaps are **recorded, not hidden**: metrics CSV includes `availability_state` (`UNAVAILABLE`, `DEGRADED`, `AVAILABLE`). Coverage is assessed over **serving rows** (`ready_replicas > 0`); `TARGET_AVAILABILITY` reports all three counts separately.

Prior run `run-20260904T220808Z` replica time series is unrecoverable (no in-run sampler); see `results/runs/run-20260904T220808Z/RECOVERY.md`.

## Measurement limitations

- **Load generator location:** Locust runs on the operator's laptop in California; load reaches `us-central1` over the public internet. Client RTT and uplink capacity are included in all reported latency figures.
- **Comparison validity:** Both arms are affected identically (same client, same region path, same LoadBalancer topology), so fixed-vs-HPA comparisons are valid. Absolute latency numbers are **not** datacenter-internal measurements.
- **Tier 2 deferral:** Running Locust in-cluster (same region as the cluster) is deferred to Tier 2 to remove client-path variance from absolute latency.

## Prometheus analysis window (published CSV rows)

- **Window:** `LOAD_START t0` through `t0 + RUN_TIME` per arm (inclusive), as recorded in each run's `manifest.json` and echoed as `ANCHOR_WINDOW_ENFORCED start=… end=…` during collection.
- **Burst included:** The first minute of load is in the published window. A 60s scrape pre-roll during the post-cold-start ready wait (`METRIC_SCRAPE_PREROLL_*` in `rep.log`) warms Prometheus scrapes before `t0`; pre-roll samples are queried but not written as CSV rows.
- **Every anchored timestamp produces a row:** 73 rows at 18m / 15s step. Rows are never dropped when Prometheus series end early.
- **Three availability states:** `UNAVAILABLE` when `ready_replicas == 0` (metric cells are `TARGET_UNAVAILABLE`). `DEGRADED` when `0 < ready_replicas < declared` (fixed arm; pods serving below capacity). `AVAILABLE` when `ready_replicas == declared` (fixed) or `ready_replicas > 0` (HPA). A cell's value must never contradict its row's `availability_state`.
- **Coverage:** `METRICS_COLUMN_COVERAGE` is over **serving rows** (`AVAILABLE` + `DEGRADED`, i.e. `ready_replicas > 0`). Rate columns skip the first two serving rows (burst-onset `MISSING`). `TARGET_AVAILABILITY` reports `rows_unavailable`, `rows_degraded`, and `rows_available` each out of `rows_total`.
- **Rate lookback:** PromQL `rate(...[N])` uses `N = max(step, 2×scrape_interval)` (30s at default 15s step/scrape).
- **Log markers:** `METRIC_QUERY_PREROLL_SEC=60 published_rows_only=true no_row_exclusions=true`

### Burst-onset MISSING rows (rate-derived columns)

Two published **available** rows per arm at the start of the window (`t0` and `t0+step`) are often `MISSING` for rate-derived columns when pods are up — inherent Prometheus counter semantics at burst onset.

- **Gauges when available:** `cpu_utilization_pct` and replica columns populate when `ready_replicas > 0`.
- **Expected coverage (available rows only):** ~71/71 (1.0) for gauges; rate columns ~69/71 (0.973) at 18m with no collapse.

## Locust failure semantics

- **`Unexpected status 0`:** In Locust logs/CSV this means the HTTP client received no response (connection error, timeout, or reset) — **not** an HTTP 5xx.
- **Exit code vs measurement:** Locust exits non-zero when any sample fails unless `--exit-code-on-error 0` is set. Request failures are the measurement under burst load; the harness treats a completed load shape with valid stats as success.
- **Prometheus `error_rate` vs Locust failure rate:** Prometheus `error_rate` measures **server-observed** non-200 responses (`app_requests_total{status_code!="200"}`). Locust measures **client-observed** outcomes including connection failures and timeouts. When the app is unreachable or overloaded, the client sees failures the server never records. **Locust is authoritative for the published failure rate.**

**Worked example (run-20260904T230444Z):**
- HPA arm: Locust **63 / 20,820 (0.30%)**; Prometheus `error_rate` **non_zero=0** for the run — consistent (connection-level failures).
- Fixed arm: Locust **1,230 / 10,193 (12.07%)**; Prometheus `error_rate` **~0.0** — same mechanism during baseline collapse.

## Scale-up lag (HPA arm)

During scale-up, `spec_replicas` (HPA desired) leads `ready_replicas` (pods passing readiness). `HPA_SCALE_FLOOR_CHECK` uses peak **`spec_replicas`** to match Kubernetes event rescale lines. The gap between spec and ready is scale-up latency — a real measurement, not missing data.

## Failure rate (fixed arm) — run-20260904T230444Z
- **Value:** **12.07%** (1230 / 10193)
- **Source:** `results/runs/run-20260904T230444Z/rep-1/locust_fixed_stats.csv`

## Failure rate (HPA arm) — run-20260904T230444Z
- **Value:** **0.30%** (63 / 20820)
- **Source:** `results/runs/run-20260904T230444Z/rep-1/locust_hpa_stats.csv`

## Throughput ratio (HPA / fixed)
- **Value:** `PENDING_ANALYSIS`
- **Formula:** `hpa_successful_requests / fixed_successful_requests`
- **Source:** Locust stats for both arms

## Cost per 1k successful requests
- **Value:** `PENDING_ANALYSIS`
- **Formula:** `(pod_hours * list_price_per_pod_hour) / (successful_requests / 1000)`

## SLO burn (14.4x tier, 1h/5m)
- **Value:** `PENDING` (Tier 3 module)

## Run-scoped error budget consumption (%)
- **Value:** `PENDING_ANALYSIS`
- **Source:** Locust failure evidence per arm
