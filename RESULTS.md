# RESULTS

Authority split:
- **Locust:** request counts, successes, failures (published failure rate), and **client-observed response time** (run-level percentiles from `locust_*_stats.csv`)
- **Prometheus:** CPU and **in-handler service time** (`app_request_latency_seconds` on `/cpu` only)
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
- **Latency percentiles (Prometheus service time):** Derived from **33 of 57** assessable serving rows (58% of the serving window where rate data exists). Treat fixed-arm Prometheus service-time figures as **indicative**, not precise, during the collapse period. Client-observed response time comes from Locust and is **not** affected by this gap (see [Latency — two metrics, never merged](#latency--two-metrics-never-merged)).

Per-row investigation table: [docs/run-20260904T230444Z-rate-gap-table.md](docs/run-20260904T230444Z-rate-gap-table.md).

### Figure generation (partial-coverage disclosure)

Published figures under `docs/figures/run-20260904T230444Z/` were **not** produced by a fully coverage-certified analysis pass. Fixed-arm Prometheus rate coverage is **33/57** (investigated; see appendix). The **service-time** figure (`latency_comparison.png`) was generated with `--allow-partial-coverage`, which bypasses `METRICS_COLUMN_COVERAGE` on the fixed metrics CSV. Client-observed figures (`latency_client_*.png`) come from Locust CSVs and are **not** affected by Prometheus coverage gaps. The tool logs `ALLOW_PARTIAL_COVERAGE=true fixed_metrics_coverage_certified=false` and records `analysis.allow_partial_coverage: true` in the run `manifest.json`.

```bash
.venv/bin/python analysis/analyze_results.py \
  --fixed results/runs/run-20260904T230444Z/rep-1/fixed_metrics.csv \
  --hpa results/runs/run-20260904T230444Z/rep-1/hpa_metrics.csv \
  --replica-series-fixed results/runs/run-20260904T230444Z/rep-1/replica_series_fixed.csv \
  --replica-series-hpa results/runs/run-20260904T230444Z/rep-1/replica_series_hpa.csv \
  --locust-fixed-stats results/runs/run-20260904T230444Z/rep-1/locust_fixed_stats.csv \
  --locust-hpa-stats results/runs/run-20260904T230444Z/rep-1/locust_hpa_stats.csv \
  --locust-fixed-history results/runs/run-20260904T230444Z/rep-1/locust_fixed_stats_history.csv \
  --locust-hpa-history results/runs/run-20260904T230444Z/rep-1/locust_hpa_stats_history.csv \
  --t0-fixed results/runs/run-20260904T230444Z/rep-1/t0_fixed.txt \
  --t0-hpa results/runs/run-20260904T230444Z/rep-1/t0_hpa.txt \
  --output-dir docs/figures/run-20260904T230444Z \
  --allow-partial-coverage
```

Do **not** treat a run whose manifest lacks `analysis.allow_partial_coverage: true` (or whose analyze log lacks `ALLOW_PARTIAL_COVERAGE=true`) as using the same figure-generation policy.

## Reproducibility

Guards enforced for this run (evidence in `rep.log` and collection output):

1. **Replica assertion (fixed arm):** peak in-window `ready_replicas` must reach declared count (3); mid-run dips log `REPLICA_DIP_OBSERVED` and continue.
2. **Replica assertion (HPA arm):** peak in-window `spec_replicas` must exceed `minReplicas`; `HPA_SCALE_FLOOR_CHECK` uses peak **spec** (HPA desired).
3. **Label isolation:** `OPPOSITE_ARM_SERIES=0` — no cross-arm Prometheus series leakage at collection time.
4. **Anchored collection window:** every 15s timestamp from `t0` through `t0+18m` written; `PROMETHEUS_SERIES_GAP … anchor_fill=true` when series end early — no row exclusions.
5. **Coverage threshold:** `MIN_COLUMN_COVERAGE_RATIO=0.95` blocks publication when serving-row coverage is insufficient (this run: PARTIAL).
6. **Three-state availability:** `UNAVAILABLE` (`ready==0`), `DEGRADED` (`0 < ready < declared`), `AVAILABLE` (`ready==declared` fixed / `ready>0` HPA); metric cells never contradict row state.

## Measurement limitations

- **Application change after run-20260904T230444Z:** `/cpu` was `async def` (CPU work on the event loop, blocking `/health` under load). It is now sync `def` (Starlette threadpool dispatch). **Future runs are not comparable to run-20260904T230444Z** — the application under test has changed.
- **Starlette threadpool (40 tokens, unchanged):** At 200m CPU a pod does roughly 2 req/s; running 40 `/cpu` requests concurrently does not add throughput — Python's GIL serialises bytecode execution. The purpose of the `/cpu` handler change is **probe availability**, not throughput. Expect per-request latency to get **worse**, not better, under saturation.
- **`psutil.cpu_percent(interval=None)`:** Returns CPU since the previous call. It is invoked from `/` and `/fail` on the event loop; `app_cpu_usage_percent` feeds `cpu_utilization_pct` in the metrics CSVs. Higher `/cpu` concurrency may make this gauge noisier (documented only; not fixed).
- **`check_event_loop_not_blocked` load generator:** The kind smoke check drives `/cpu` load from a Python harness run **inside** the application container via `kubectl exec`. That harness shares the pod's cgroup (200m CPU, 256Mi memory — same limits as the app) and its allocations count against the pod budget. Measured `/health` latency under this check is therefore a **conservative upper bound**, not a faithful reproduction of external load at equivalent request rate (where the app would have the full cgroup budget). Tier 2 backlog: drive load from outside the pod for a more faithful probe-availability test.
- **Probe values are conservative, not measured optima:** Kind in-container load has run-to-run `/health` variance far larger than between-config differences (five repeat runs at identical config and 15 threads/pod: `/health` max range **715–3992 ms**, a **5.6×** spread; see `PROGRESS.md` readiness-repeat-control). The readiness sweep (99faf65) is **withdrawn** as uninformative — between-config spread was 593–918 ms, roughly five times smaller than the noise. A1's **590.9 ms** and A2 probe values derived from it are single samples from that distribution. **GKE with 80 external Locust users** is the measurement of record for probe behavior.
- **Readiness probe (current):** `timeoutSeconds: 1`, `failureThreshold: 6`, `periodSeconds: 5` — a conservative choice against symmetric-overload EndpointSlice cascade (six consecutive failures ≈ 30s sustained unresponsiveness before removal), not a kind-measured optimum. `startupProbe` and `livenessProbe` unchanged from A2.
- **Load generator location:** Locust runs on the operator's laptop in California; load reaches `us-central1` over the public internet. Client RTT and uplink capacity are included in **client-observed** response time (Locust). Prometheus service time measures in-handler compute only after the request is accepted.
- **Comparison validity:** Both arms are affected identically (same client, same region path, same LoadBalancer topology), so fixed-vs-HPA comparisons are valid. Absolute latency numbers are **not** datacenter-internal measurements.
- **Tier 2 deferral:** Running Locust in-cluster (same region as the cluster) is deferred to Tier 2 to remove client-path variance from absolute latency.

### Baseline calibration (Tier 2 Phase A, A8)

- **`run-20260905T160157Z` is non-comparable to all future runs.** Its HPA arm ran `minReplicas: 1` against a fixed arm declared at 3, so the HPA arm began at one third the capacity and paid scale-up queueing the fixed arm never incurred. Evidence: `results/runs/run-20260905T160157Z/rep-1/replica_series_hpa.csv` reaches a minimum `spec_replicas` of **1** across its 70 samples. `k8s/hpa.yaml` now sets `minReplicas: 3`, so both arms start at equal capacity and the only remaining difference is HPA's ability to scale **up**. This removes the minimum-size confound; it does not make the earlier run wrong, it makes it a different experiment.
- **Why this matters (uncalibrated baseline).** RLScale-Bench (arXiv:2605.26418) names this as a gap that makes comparisons unreliable: "When RL studies compare against an uncalibrated baseline, apparent improvements may reflect baseline weakness rather than algorithmic gains." The A7 result — fixed beating HPA on p95, p99 and cost — was measured against exactly such a baseline.
- **Narrow workload coverage.** A7 used one load shape (ramp-and-hold). The same paper tests six and reports "rankings shifting by up to four positions between steady-state and bursty traffic", with the calibrated baseline winning on steady traffic and losing on bursty/flash. The "fixed wins" conclusion may therefore be specific to this shape, which is why A10 adds `constant` and `flash`.

#### Load level derivation (Little's law)

Concurrency = throughput × mean response time. Both inputs come from A7 artifacts:

- R = **1039.8 ms** — Aggregated `Average Response Time` in `results/runs/run-20260905T160157Z/rep-1/locust_fixed_stats.csv`.
- W = **2.0 s** — mean of `wait_time = between(1, 3)` in `locust/locustfile.py`.

One user therefore delivers 1/(R+W) = 1/3.04 = **0.329 req/s = 19.7 req/min**.

- **Rejected — the paper's absolute req/min.** Their 80 → 240 → 80 req/min converts to 4.1 → 12.2 → 4.1 users. Their figures describe their simulated service's capacity, not this one. At 4–12 users, three 200m pods never approach the 60% CPU target and the HPA arm would abort with `HPA_NEVER_SCALED`. We preserve their **shape and ratio**, not their absolutes.
- **Rejected — 60 → 180 users.** 180 users demands 180 × 0.35 ≈ **63 RPS**. A7's fixed arm sustained **15.7 RPS** on 3 pods (16,928 requests ÷ 1,080 s), about 5.2 RPS/pod, so 63 RPS needs roughly **12 pods** — above `maxReplicas: 10`. Both arms would saturate and the run would measure overload, not autoscaling.

**Assumption and sensitivity.** R is drawn from the very run this section declares non-comparable. With `minReplicas: 3` there is no scale-up queueing, so R will **fall**, and each user will therefore deliver **more** load than the estimate assumes:

- If R falls to 500 ms, per-user rate rises from 1/(1.04+2.0) = 0.329 to 1/(0.5+2.0) = **0.400 req/s, a 22% increase**.
- Flash peak of 90 users then demands 90 × 0.400 = **~36 RPS**, about **7 pods** at 5.2 RPS/pod — still inside `maxReplicas: 10`.
- The chosen levels are therefore **safe under the expected shift**, but they are calibrated from a superseded run and **must be re-derived after the first calibrated GKE run**.

#### Time-weighted mean user count per shape

Reported so that cross-shape differences in total volume are visible rather than hidden. All three shapes are exactly **18 minutes (1080 s)**: duration is held constant because unequal durations make pod-hours incommensurable and break the cost-per-1k comparison.

| Shape | Curve | Time-weighted mean users |
|---|---|---|
| hybrid | 1→20, 20→80, hold 60, 60→5 | **45.5** — (180×10.5 + 180×50 + 540×60 + 180×32.5)/1080 |
| constant | 45 flat, ±10% seeded noise | **45.4167** — realized value of the 36 seeded plateaus (`NOISE_SEED=1729`, band 41–49); design centre is 45.0 and the seed lands 0.42 above it |
| flash | 30 → 90 → 30 | **40.0** — (420×30 + 180×90 + 480×30)/1080 |

Ramp segments are credited at midpoint concurrency.

**Locust ignores `--run-time` when a `LoadTestShape` is present** (it warns: "--run-time, --users or --spawn-rate have no impact on LoadShapes unless the shape class explicitly reads them"). Each shape therefore terminates itself by returning `None` from `tick()` at 1080 s, and the harness `--run-time 18m` plus its 60 s wall-clock guard remain the outer bound rather than the mechanism. The two agree by construction; if a shape's internal duration is ever changed, `--run-time` will not correct it.

#### Prediction recorded before the calibrated runs

A7's HPA arm averaged **8.16** ready replicas, recomputed from `results/runs/run-20260905T160157Z/rep-1/replica_series_hpa.csv`: pod-hours 2.4289 over a 1,071 s span, so 2.4289 × 3600 ÷ 1071 = 8.16 time-weighted (simple mean over the 70 samples is 8.07). Hybrid's time-weighted mean is 45.5 users — the same load level as `constant` — so **expect roughly 6–8 replicas on constant**.

If `constant` instead holds at 3, that is **the finding, not an error**. It is what RLScale-Bench predicts for a calibrated baseline on steady-state traffic ("zero constraint violations on steady-state traffic"), and aborting on it would convert a result into a failure. Collection reports `HPA_DID_NOT_SCALE_ON_STEADY_LOAD peak_spec=<n> minReplicas=<n>` and continues. Only `hybrid` and `flash`, which must scale, keep `HPA_NEVER_SCALED` as an abort — see `--hpa-no-scale-policy` in `analysis/collect_metrics.py`, which defaults to `abort` so no shape opts into leniency implicitly. Recording the prediction before the run is what makes either outcome interpretable.

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

## Latency — two metrics, never merged

Published latency is **two separate metrics**. Do not compare or average them.

### Response time (client-observed, Locust)

**Authority:** Locust `locust_*_stats.csv` Aggregated row. Includes queueing, connection setup, TLS, and failures — the full wall-clock time from the load generator's perspective.

| Arm | p50 (ms) | p95 (ms) | p99 (ms) | Failure share | Source |
|-----|---------:|---------:|---------:|--------------:|--------|
| **Fixed** | **1,200** | **21,000** | **40,000** | **12.07%** (1230 ÷ 10193) | `locust_fixed_stats.csv` |
| **HPA** | **310** | **1,300** | **2,200** | **0.30%** (63 ÷ 20820) | `locust_hpa_stats.csv` |

**Failure-inclusive percentiles.** Locust logs response time for every request before recording a failure (`runners.py:126-129`: `log_request` is unconditional; `log_error` at `stats.py:412-415` increments only `num_failures`). The stats CSV has **no success-only percentile columns**. `success_only_percentiles_available: false` in `locust_summary.json`.

**No configured timeout.** `locust/locustfile.py` sets no `timeout=` on its requests. A connection-level failure (`status_code = 0`) records the full duration until the underlying exception was raised, bounded only by the OS/socket default — not by any value we chose. Failed requests contribute their real elapsed time to the percentiles. The fixed arm's upper percentiles therefore include waits terminated by the network stack (`Max Response Time` **48,293 ms**). The HPA arm's run max (**57,511 ms**) is accounted for separately under [Scale-up lag (HPA arm)](#hpa-run-max-response-time-57511-ms).

**Distribution-free bound on success-only p95** (not an estimate). With failure fraction `f`, the success at success-rank 0.95 lies between combined percentiles `0.95 × (1 − f)` and `f + 0.95 × (1 − f)`:

| Arm | f | Percentile interval | Value bracket (grid columns only, no interpolation) |
|-----|--:|--------------------:|-----------------------------------------------------|
| Fixed | 0.120671 | **p83.5363 – p95.6034** | ≥ p80 (**2,600 ms**) and ≤ p98 (**34,000 ms**) |
| HPA | 0.003026 | **p94.7125 – p95.0151** | ≥ p90 (**960 ms**) and ≤ p98 (**1,800 ms**) |

The bound is wide for the collapsing fixed arm and nearly tight for the healthy HPA arm — the HPA combined p95 of 1,300 ms is effectively its success-only p95.

**Rejected approach (do not re-propose):** filtering `locust_*_stats_history.csv` to windows where cumulative `Total Failure Count` did not increase would yield windows with no failures, but those windows are preferentially the low-load periods before the spike and after recovery — coordinated omission in another form. Not used.

**Sliding-window figure (`latency_client_window.png`):** Percentile columns in `locust_*_stats_history.csv` are **not** cumulative run percentiles. Locust documents them as "current response time … sliding window of (approximately) the last 10 seconds" (`CURRENT_RESPONSE_TIME_PERCENTILE_WINDOW = 10` in Locust 2.46.4 `stats.py`). Run-level headline numbers come from `locust_*_stats.csv` only; history is plotted as a time series and never aggregated (`assert_no_run_level_percentile_from_history` guard).

### Service time (in-handler, Prometheus)

**Authority:** `histogram_quantile` over `app_request_latency_seconds_bucket` in `fixed_metrics.csv` / `hpa_metrics.csv`. Times only `compute_primes()` inside the `/cpu` handler — after the request is accepted and dequeued. Never records a request that timed out or hit a dead pod.

| Arm | Mean p50 (ms) | Mean p95 (ms) | Mean p99 (ms) | Populated rows | Source |
|-----|-------------:|--------------:|--------------:|---------------:|--------|
| **Fixed** | 133 | 239 | 253 | 33/57 assessable serving rows | `fixed_metrics.csv` |
| **HPA** | 148 | 239 | 251 | full window | `hpa_metrics.csv` |

**Documented limitations (not fixed in this pass):**

- **`GET /` is not instrumented.** `app/main.py` does not observe `REQUEST_LATENCY` or `REQUEST_COUNT` on the health-check route — **20% of offered traffic** is invisible server-side. This affects `rps` and `error_rate` as well as service-time percentiles.
- **Histogram bucket resolution.** Buckets `[..., 0.1, 0.25, 0.5, ...]` (`app/main.py:38`) place the published p95 of ~237 ms inside the single 0.1–0.25 s bucket, making it a linear interpolation within one bucket rather than a resolved measurement.

## Scale-up lag (HPA arm)

During scale-up, `spec_replicas` (HPA desired) leads `ready_replicas` (pods passing readiness). `HPA_SCALE_FLOOR_CHECK` uses peak **`spec_replicas`** to match Kubernetes event rescale lines. The gap between spec and ready is scale-up latency — a real measurement, not missing data.

### HPA run max response time (57,511 ms)

The HPA Aggregated `Max Response Time` (**57,511 ms**) exceeds the fixed arm's (**48,293 ms**) even though HPA's failure share is **0.30%** vs fixed **12.07%**. This is not a contradiction in the percentile headline (p95 **1,300 ms**): the run max is a single sample, and the 63rd failure is the only request at that duration.

**When it completed.** First `locust_hpa_stats_history.csv` Aggregated row where cumulative `Total Max Response Time` reaches **57,511 ms**: epoch **1788565380** = **2026-09-04T23:43:00Z** (`User Count` **5**, recovery phase). On that same row `Total Failure Count` increases **62 → 63** — this sample is the **final** HPA failure. `locust_hpa_failures.csv` records `CatchResponseError('Unexpected status 0')` on `GET /cpu?intensity=low` with **Last Seen 2026-09-04T23:43:00Z**.

**Replica state (does not coincide with scale-up).** In the **90 seconds before 23:43:00Z**, `replica_series_hpa.csv` shows **`spec_replicas` decreases**, not increases: **10 → 7** at 23:41:52Z, **7 → 5** at 23:42:07Z, **5 → 3** at 23:42:53Z. No `spec_replicas` increase appears in that window. Closest in-run samples:

| Source | Timestamp | `spec_replicas` / `replicas` | `ready_replicas` |
|--------|-----------|------------------------------|------------------|
| `replica_series_hpa.csv` | 2026-09-04T23:42:07Z | spec **5** | ready **5** |
| `replica_series_hpa.csv` | 2026-09-04T23:42:53Z | spec **3** | ready **3** |
| `hpa_metrics.csv` | 2026-09-04T23:42:06Z (elapsed 1041s) | replicas **5** | ready **5** |
| `hpa_metrics.csv` | 2026-09-04T23:43:06Z (elapsed 1065s) | replicas **2** | ready **2** |

**Most likely mechanism: scale-in disruption (consistent-with, not proven).** The manifests define **no** `lifecycle.preStop` hook and **no** explicit `terminationGracePeriodSeconds` on either deployment (`k8s/deployment-fixed.yaml`, `k8s/deployment-hpa.yaml`); the pod spec goes from `readinessProbe` directly to `env`, so shutdown uses the Kubernetes default **30s** grace only. Neither LoadBalancer Service sets connection-draining or backend-timeout annotations (`k8s/service.yaml` has no `metadata.annotations` on either Service).

On graceful shutdown, Kubernetes marks the pod **terminating**, removes it from **ready** EndpointSlices (`ready=false`), but leaves it in **serving** EndpointSlices (`serving=true`) so load balancers can drain in-flight connections ([Pod termination](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination)). With **no** `preStop` sleep, **no** LB draining annotation, and the default 30s grace, an in-flight client request can still be routed to a pod being torn down during scale-in. That fits this sample: a **single** `status_code = 0` failure completing at **23:43:00Z** while `spec_replicas` was falling **10 → 7 → 5 → 3** in the prior 68s (recovery phase, 5 users).

**Limit of the claim.** The committed artifacts **do not identify which pod served this request** — no per-request server log, no pod termination timestamp correlated to this Locust sample. The config gap plus timing during scale-in make scale-in disruption the **most likely** mechanism; it is **not proven** for this specific request.

**Tier 2 backlog.** Scale-**in** disruption is a measurable and rarely studied cost of autoscaling; this run produced **one** client-observed instance (63rd failure, 57,511 ms). Tier 2 Phase B should instrument **both** scale-out and scale-in (EndpointSlice transitions, pod deletion timestamps, in-flight request correlation) — not scale-out alone.

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

**Formula:** `(pod_hours × list_price_per_pod_hour) / (successful_requests / 1000)`

#### Cost model assumptions (not billed spend)

- **`$0.0535/hr`** — hardcoded published on-demand list price for GKE worker SKU **`e2-standard-2`**, region **`us-central1`**, checked **2026-09-04** for this publication. Defined in `analysis/analyze_results.py` (`GKE_E2_STANDARD_2_USD_PER_HOUR`). **Not fetched** from Cloud Billing or a `pricing/` catalog (Tier 2 deferred).
- **`$0.002675` per pod-hour** — prorated by CPU request share **0.1 / 2.0 vCPU** on that node type (`LIST_PRICE_PER_POD_HOUR`).
- **Modeled cost only** — reflects **relative pod-hour efficiency** between arms at published list rates; **not invoiced spend**.
- **Pod-hours** — integrated from in-run `replica_series_*.csv`: `sum(ready_i × Δt) / 3600`. Fixed arm **0.447** pod-hours, **not** the naive `3 × 0.3h = 0.900`, because the deployment **collapsed to 0 ready replicas** for much of the window.

| Arm | pod_hours | successful (Locust) | Cost per 1k successful | Source |
|-----|----------:|--------------------:|-----------------------:|--------|
| Fixed | **0.447** | **8,963** (10193 − 1230) | **$0.000133** | `replica_series_fixed.csv` + `locust_fixed_stats.csv` |
| HPA | **2.411** | **20,757** (20820 − 63) | **$0.000311** | `replica_series_hpa.csv` + `locust_hpa_stats.csv` |

**Arithmetic check:** HPA used **5.39×** pod-hours (2.411 ÷ 0.447) and delivered **2.32×** successful requests (20757 ÷ 8963) → HPA cost per 1k successful is **2.33×** fixed (5.39 ÷ 2.32), not cheaper. The prior chart was wrong: it divided pod cost by `sum(prometheus_rps)×15` (server-side, incomplete during collapse) instead of Locust successes, and assumed fixed always ran 3 replicas (0.900 pod-hours) rather than measuring collapse (0.447 pod-hours).

**Trade stated plainly:** HPA bought reliability — failure rate **12.07% → 0.30%** — at a **~133% compute premium per successful request** (~2.33×). That is a defensible trade, not a cost saving.

### SLO burn (14.4× tier, 1h/5m)
- **Value:** not computed (Tier 3 module)

### Run-scoped error budget consumption (%)
- **Fixed:** 12.07% of requests failed (Locust) — 1230 / 10193
- **HPA:** 0.30% of requests failed (Locust) — 63 / 20820
- **Source:** `locust_*_stats.csv` Aggregated rows

## Figures

Generated with `--allow-partial-coverage` for the Prometheus service-time figure only (see [Data completeness](#figure-generation-partial-coverage-disclosure)). Fixed arm PARTIAL on Prometheus metrics; HPA arm complete. Client-observed figures are Locust-sourced.

- `docs/figures/run-20260904T230444Z/latency_client_run_level.png` — client-observed p50/p95/p99 (headline)
- `docs/figures/run-20260904T230444Z/latency_client_window.png` — client-observed, 10-second sliding window
- `docs/figures/run-20260904T230444Z/latency_comparison.png` — service time (in-handler, Prometheus)
- `docs/figures/run-20260904T230444Z/throughput_comparison.png`
- `docs/figures/run-20260904T230444Z/cpu_replicas.png`
- `docs/figures/run-20260904T230444Z/cost_performance.png`

Original run artifacts: `results/runs/run-20260904T230444Z/rep-1/figures/`.
