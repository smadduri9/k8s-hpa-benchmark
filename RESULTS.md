# RESULTS

Authority split:
- **Locust:** request counts, successes, failures (published failure rate)
- **Prometheus:** CPU and timing (server-side observations only)
- **kubectl in-run sampling:** `spec_replicas`, `status_replicas`, `ready_replicas` in `replica_series_<arm>.csv` and metrics CSV

## Headline — run-20260904T230444Z (production, GKE)

**Status: PARTIAL** — see [Data completeness](#data-completeness). The fixed 3-replica baseline **collapsed under burst** (ready replicas hit **0**; liveness kills and crash loops). It was not merely slow. HPA scaled to **10** replicas and sustained service. Availability gaps are recorded in `fixed_metrics.csv`, not hidden.

| Arm | Requests | Failures | Failure rate | Replica outcome | Source |
|-----|---------:|---------:|-------------:|-----------------|--------|
| **Fixed** | 10,193 | 1,230 | **12.07%** (1230 ÷ 10193) | Declared **3**; ready hit **0** | `results/runs/run-20260904T230444Z/rep-1/locust_fixed_stats.csv` |
| **HPA** | 20,820 | 63 | **0.30%** (63 ÷ 20820) | Peak **spec=10**, peak **ready=10** | `results/runs/run-20260904T230444Z/rep-1/locust_hpa_stats.csv` |

**Fixed availability** (73 anchored rows, 15s step, 18m window): **14 UNAVAILABLE** / **40 DEGRADED** / **19 AVAILABLE** — from `results/runs/run-20260904T230444Z/rep-1/fixed_metrics.csv` (`availability_state` column), cross-checked against `replica_series_fixed.csv`.

**HPA scale check:** `HPA_SCALE_FLOOR_CHECK peak=10 minReplicas=1 peak_ready=10` — from `results/runs/run-20260904T230444Z/rep-1/rep.log`; replica evidence in `replica_series_hpa.csv`.

**Throughput (successful requests):** HPA served **20,757** successes vs fixed **8,963** → ratio **2.32×** (20757 ÷ 8963). Source: `locust_summary.json` (derived from both `locust_*_stats.csv` Aggregated rows).

Prior run `run-20260904T220808Z` replica time series is unrecoverable (no in-run sampler); see `results/runs/run-20260904T220808Z/RECOVERY.md`.

## Data completeness

This run is **PARTIAL**, not failed unexplained. Metrics collection completed and was recovered post-classification; the publication gate stopped on the **first** column below threshold.

### Why PARTIAL

`METRICS_COVERAGE_BELOW_THRESHOLD column=cpu_utilization_pct populated=56/59 ratio=0.9492 threshold=0.95` — recorded in `results/runs/run-20260904T230444Z/STATUS`. The gate evaluates columns in order and **reports only the first failure**, so the trivial CPU gap (3 cells) masked the larger rate gap in the abort message. The gate behavior is documented here; it is **not changed** in this pass.

### Coverage on serving rows

Serving rows = `ready_replicas > 0` (**59** of 73 anchored rows: 40 DEGRADED + 19 AVAILABLE).

| Column | Populated | Denominator | Ratio | Gate used? |
|--------|----------:|------------:|------:|:------------:|
| `cpu_utilization_pct` | 56 | **59** (all serving rows) | 0.9492 | **Yes — abort** |
| Rate-derived (`latency_*`, `rps`, `error_rate`) | 33 | **57** (59 serving − 2 burst-onset rows) | 0.5789 | Would fail next |
| Rate-derived (alternate count) | 33 | **59** (all serving rows, no burst exclusion) | 0.5593 | Not used by gate |

**Denominator discrepancy:** A reader counting serving rows in the CSV finds **59**. The gate uses **57** for rate columns because `metrics_contract.py` excludes the first two serving rows — the documented counter-warmup rows at **`t0` and `t0+step`** (`2026-09-04T23:06:04Z` and `2026-09-04T23:06:19Z`), where rate-derived cells are `MISSING` by Prometheus counter semantics (see [Burst-onset MISSING rows](#burst-onset-missing-rows-rate-derived-columns)). Impact on the conclusion is negligible (33/57 = 0.5789 vs 33/59 = 0.5593).

### What the gaps mean

- **Rate coverage 33/57:** 24 assessable serving rows have `MISSING` rate-derived cells. **21/24** coincide with replica transition or pod restart (`Killing`/`Started`/`BackOff`/`Created`) within the prior 30s. The remaining **3/24** lack that strict correlation but have direct Prometheus evidence of **≤1** `app_requests_total` counter sample in the 30s rate window — `rate()` correctly returns empty after series restart/staleness during crash loops.
- **CPU coverage 56/59:** 3 serving rows have `MISSING` `cpu_utilization_pct` at collapse/recovery boundaries (gauge series not yet scraped after `ready_replicas` recovery from 0).
- **No collection-defect signature:** Zero rows show a populated CPU gauge with capturable rate data that collection missed.
- **Latency percentiles:** Derived from **33 of 57** assessable serving rows (58% of the serving window where rate data exists). Treat fixed-arm latency figures as **indicative**, not precise, during the collapse period.

Per-row investigation table: [docs/run-20260904T230444Z-rate-gap-table.md](docs/run-20260904T230444Z-rate-gap-table.md).

## Reproducibility

Guards enforced for this run (evidence in `rep.log` and collection output):

1. **Replica assertion (fixed arm):** peak in-window `ready_replicas` must reach declared count (3); mid-run dips log `REPLICA_DIP_OBSERVED` and continue.
2. **Replica assertion (HPA arm):** peak in-window `spec_replicas` must exceed `minReplicas`; `HPA_SCALE_FLOOR_CHECK` uses peak **spec** (HPA desired).
3. **Label isolation:** `OPPOSITE_ARM_SERIES=0` — no cross-arm Prometheus series leakage at collection time.
4. **Anchored collection window:** every 15s timestamp from `t0` through `t0+18m` written; `PROMETHEUS_SERIES_GAP … anchor_fill=true` when series end early — no row exclusions.
5. **Coverage threshold:** `MIN_COLUMN_COVERAGE_RATIO=0.95` blocks publication when serving-row coverage is insufficient (this run: PARTIAL).
6. **Three-state availability:** `UNAVAILABLE` (`ready==0`), `DEGRADED` (`0 < ready < declared`), `AVAILABLE` (`ready==declared` fixed / `ready>0` HPA); metric cells never contradict row state.

## Measurement limitations

- **Load generator location:** Locust runs on the operator's laptop in California; load reaches `us-central1` over the public internet. Client RTT and uplink capacity are included in all reported latency figures.
- **Comparison validity:** Both arms are affected identically (same client, same region path, same LoadBalancer topology), so fixed-vs-HPA comparisons are valid. Absolute latency numbers are **not** datacenter-internal measurements.
- **Tier 2 deferral:** Running Locust in-cluster (same region as the cluster) is deferred to Tier 2 to remove client-path variance from absolute latency.

## Prometheus analysis window (published CSV rows)

- **Window:** `LOAD_START t0` through `t0 + RUN_TIME` per arm (inclusive), as recorded in each run's `manifest.json` and echoed as `ANCHOR_WINDOW_ENFORCED start=… end=…` during collection.
- **Burst included:** The first minute of load is in the published window. A 60s scrape pre-roll during the post-cold-start ready wait (`METRIC_SCRAPE_PREROLL_*` in `rep.log`) warms Prometheus scrapes before `t0`; pre-roll samples are queried but not written as CSV rows.
- **Every anchored timestamp produces a row:** 73 rows at 18m / 15s step. Rows are never dropped when Prometheus series end early.
- **Three availability states:** `UNAVAILABLE` when `ready_replicas == 0` (metric cells are `TARGET_UNAVAILABLE`). `DEGRADED` when `0 < ready_replicas < declared` (fixed arm; pods serving below capacity). `AVAILABLE` when `ready_replicas == declared` (fixed) or `ready_replicas > 0` (HPA). A cell's value must never contradict its row's `availability_state`.
- **Coverage:** `METRICS_COLUMN_COVERAGE` is over **serving rows** (`AVAILABLE` + `DEGRADED`, i.e. `ready_replicas > 0`). Rate columns skip the first two serving rows (burst-onset `MISSING`) in the gate. `TARGET_AVAILABILITY` reports `rows_unavailable`, `rows_degraded`, and `rows_available` each out of `rows_total`.
- **Rate lookback:** PromQL `rate(...[N])` uses `N = max(step, 2×scrape_interval)` (30s at default 15s step/scrape).
- **Log markers:** `METRIC_QUERY_PREROLL_SEC=60 published_rows_only=true no_row_exclusions=true`

### Burst-onset MISSING rows (rate-derived columns)

Two published **serving** rows at the start of the window (`t0` and `t0+step`) are `MISSING` for rate-derived columns when pods are up — inherent Prometheus counter semantics at burst onset. These are the two rows excluded from the rate-column coverage denominator (59 → 57).

- **Gauges when serving:** `cpu_utilization_pct` and replica columns populate when `ready_replicas > 0`.
- **Healthy-run expectation (no collapse):** gauge coverage ~1.0 on serving rows; rate columns ~0.97 on assessable serving rows after burst exclusion.

## Locust failure semantics

- **`Unexpected status 0`:** In Locust logs/CSV this means the HTTP client received no response (connection error, timeout, or reset) — **not** an HTTP 5xx.
- **Exit code vs measurement:** Locust exits non-zero when any sample fails unless `--exit-code-on-error 0` is set. Request failures are the measurement under burst load; the harness treats a completed load shape with valid stats as success.
- **Prometheus `error_rate` vs Locust failure rate:** Prometheus `error_rate` measures **server-observed** non-200 responses (`app_requests_total{status_code!="200"}`). Locust measures **client-observed** outcomes including connection failures and timeouts. When the app is unreachable or overloaded, the client sees failures the server never records. **Locust is authoritative for the published failure rate.**

**Worked example (run-20260904T230444Z):**
- HPA arm: Locust **63 / 20,820 (0.30%)**; Prometheus `error_rate` **non_zero=0** for the run — consistent (connection-level failures).
- Fixed arm: Locust **1,230 / 10,193 (12.07%)**; Prometheus `error_rate` **~0.0** — same mechanism during baseline collapse.

## Scale-up lag (HPA arm)

During scale-up, `spec_replicas` (HPA desired) leads `ready_replicas` (pods passing readiness). `HPA_SCALE_FLOOR_CHECK` uses peak **`spec_replicas`** to match Kubernetes event rescale lines. The gap between spec and ready is scale-up latency — a real measurement, not missing data.

## Derived metrics — run-20260904T230444Z

### Failure rate (fixed arm)
- **Value:** **12.07%** — 1230 failures ÷ 10193 requests
- **Source:** `results/runs/run-20260904T230444Z/rep-1/locust_fixed_stats.csv` (Aggregated row)

### Failure rate (HPA arm)
- **Value:** **0.30%** — 63 failures ÷ 20820 requests
- **Source:** `results/runs/run-20260904T230444Z/rep-1/locust_hpa_stats.csv` (Aggregated row)

### Throughput ratio (HPA / fixed, successful requests)
- **Value:** **2.32×** — 20757 ÷ 8963
- **Source:** `results/runs/run-20260904T230444Z/rep-1/locust_summary.json`

### Cost per 1k successful requests
- **Value:** not computed (Tier 2 — requires pod-hour accounting module)
- **Formula:** `(pod_hours × list_price_per_pod_hour) / (successful_requests / 1000)`

### SLO burn (14.4× tier, 1h/5m)
- **Value:** not computed (Tier 3 module)

### Run-scoped error budget consumption (%)
- **Fixed:** 12.07% of requests failed (Locust) — 1230 / 10193
- **HPA:** 0.30% of requests failed (Locust) — 63 / 20820
- **Source:** `locust_*_stats.csv` Aggregated rows

## Figures

Generated from recovered metrics (fixed arm PARTIAL; HPA arm complete):

- `docs/figures/run-20260904T230444Z/latency_comparison.png`
- `docs/figures/run-20260904T230444Z/throughput_comparison.png`
- `docs/figures/run-20260904T230444Z/cpu_replicas.png`
- `docs/figures/run-20260904T230444Z/cost_performance.png`

Original run artifacts: `results/runs/run-20260904T230444Z/rep-1/figures/`.
