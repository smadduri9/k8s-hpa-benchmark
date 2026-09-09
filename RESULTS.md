# RESULTS

Authority split:
- **Locust:** request counts, successes, failures (published failure rate), and **client-observed response time** (run-level percentiles from `locust_*_stats.csv`)
- **Prometheus:** CPU and **in-handler service time** (`app_request_latency_seconds` on `/cpu` only)
- **kubectl in-run sampling:** `spec_replicas`, `status_replicas`, `ready_replicas` in `replica_series_<arm>.csv` and metrics CSV

## Calibrated results (minReplicas=3 both arms)

Both arms started at equal capacity (`minReplicas=3`). **Aggregate figures are pending** — per-rep charts are not published here.

### hybrid — `run-20260905T220046Z-hybrid` (n=6)

| Metric | Fixed (median) | HPA (median) | HPA slower | p (two-sided) |
|--------|---------------:|-------------:|-----------:|--------------:|
| client_p50_ms | 895 | 370 | 0/6 | 0.031250 |
| client_p95_ms | 3250 | 1950 | 0/6 | 0.062500 (n=5, one tie) |
| client_p99_ms | 4200 | 2800 | 1/6 | 0.062500 |
| failure_rate | 0.000124 | 0.000163 | — | 0.562500 (not significant) |
| pod_hours | 0.890417 | 2.493750 | — | 0.031250 |
| cost_per_1k | 0.000147246 | 0.000339106 | — | 0.031250 |

`P_FLOOR n=6 min_attainable_two_sided_p=0.031250` — p=0.031250 is the floor at n=6, not a finer significance claim.

### constant — `run-20260906T050515Z-constant` (n=3)

| Metric | Fixed (median) | HPA (median) | HPA slower | p (two-sided) |
|--------|---------------:|-------------:|-----------:|--------------:|
| client_p50_ms | 450 | 270 | 0/3 | 0.250000 |
| client_p95_ms | 1500 | 1100 | 0/3 | 0.250000 |
| client_p99_ms | 2100 | 1600 | 0/3 | 0.250000 |
| failure_rate | 0 | 0.00010008 | — | 0.250000 |
| pod_hours | 0.891667 | 2.723060 | — | 0.250000 |
| cost_per_1k | 0.000125359 | 0.000363458 | — | 0.250000 |

`P_FLOOR n=3 min_attainable_two_sided_p=0.250000` — every p-value in this table is the floor at n=3; no row can reach significance by construction.

### flash — `run-20260906T201803Z-flash` (n=2 of 3)

**STATUS: PARTIAL** — 2 of 3 repetitions executed; rep-3 never started. rep-2's Prometheus-derived cells are all `MISSING` because the TSDB was wiped before recovery. Locust data is valid. No aggregates published in this pass.

## SLO and error budget (calibrated runs)

**SLO:** **99%** of valid **`GET /cpu?intensity=low`** requests complete with client-observed response time **faster than 500 ms**. The 18-minute benchmark run is a **measurement sample**; the **30-day compliance window** is defined in [`docs/error-budget-policy.md`](docs/error-budget-policy.md).

**Retroactive SLI (existing runs):** exact count-based SLI is **not computable** — metrics CSVs store `histogram_quantile` results only (`latency_p50_ms` / `p95` / `p99`), not raw `app_request_latency_seconds_bucket` counts. Approximate SLI uses the Locust percentile grid on the **`GET,/cpu?intensity=low`** row (`analysis/sli_locust_grid.py`), labelled **approximate**, **failure-inclusive**, **no interpolation**. Brackets use adjacent grid columns only; the grid has no column below **50%**, so thresholds below the p50 cell can only be bounded as **fewer than 50%**.

**Forward SLI (Phase 5):** raw bucket counts in the collected schema — see [`docs/phase5-bucket-schema.md`](docs/phase5-bucket-schema.md).

**Headline threshold 500 ms** — three independent reasons:

1. **500 ms** is an exact Histogram bucket boundary (`app/main.py`: `0.5` s).
2. It falls between calibrated hybrid client p50 medians already published (**895 ms** fixed, **370 ms** HPA).
3. It matches the SLA used by ScalerEval (arXiv:2504.08308), a published autoscaler testbed.

**Scope:** `/cpu` only. `GET /` is not instrumented in `app_request_latency_seconds` and does not increment `app_requests_total`; Locust offers roughly **80%** of traffic to `/cpu` (`@task(4)` vs `@task(1)`).

**Headline finding (99% at 500 ms):** **Neither arm meets the SLO in any calibrated repetition** (hybrid n=6, constant n=3). Fixed arms are **fewer than 50%** faster than 500 ms in every rep — all six hybrid reps and all three constant reps. HPA arms do better on some reps but still miss by a wide margin: in hybrid, the best bracket observed is **>50% and <=66%** (reps 2, 3, 4, 6); in constant, the best is **>66% and <=75%** (rep-1). The error budget is exhausted many times over in every run (for example hybrid rep-1: **>= 5000%** consumed; hybrid rep-2 HPA: **>= 3400% and < 5000%**). This qualifies the [calibrated latency table](#calibrated-results-minreplicas3-both-arms) above: HPA's lower `client_p50_ms` medians do not imply a 500 ms tail-SLO win.

**Why the SLI brackets can look inconsistent with `client_p50_ms`:** the calibrated table uses Locust's **Aggregated** row, which mixes **`GET /`** (trivial, ~20% of traffic) with **`GET /cpu?intensity=low`** (the expensive ~80%). The SLI below is scoped to the **`GET,/cpu?intensity=low`** row only. That is why HPA can show **`client_p50_ms` 370** in the hybrid calibrated table while the SLI reports **fewer than 50% faster than 500 ms** for some reps. Both numbers are correct over different populations; neither contradicts the other.

### Threshold curve — hybrid `run-20260905T220046Z-hybrid` rep-1 (illustrative)

| Threshold (ms) | Fixed — faster than (approx.) | Fixed — miss rate (approx.) | HPA — faster than (approx.) | HPA — miss rate (approx.) |
|---------------:|------------------------------|----------------------------|----------------------------|--------------------------|
| 100 | fewer than 50% | >= 50% | fewer than 50% | >= 50% |
| 250 | fewer than 50% | >= 50% | fewer than 50% | >= 50% |
| **500** | fewer than 50% | >= 50% | fewer than 50% | >= 50% |
| 1000 | >50% and <=66% | >=34% and <50% | >50% and <=66% | >=34% and <50% |
| 2500 | >90% and <=95% | >=5% and <10% | >90% and <=95% | >=5% and <10% |
| 5000 | >99.9% and <=99.99% | >=0.01% and <0.10% | >99% and <=99.9% | >=0.10% and <1% |

Rep-to-rep variance at **500 ms** (all six reps):

| Rep | Fixed — faster than | HPA — faster than |
|----:|--------------------|--------------------|
| 1 | fewer than 50% | fewer than 50% |
| 2 | fewer than 50% | >50% and <=66% |
| 3 | fewer than 50% | >50% and <=66% |
| 4 | fewer than 50% | >50% and <=66% |
| 5 | fewer than 50% | fewer than 50% |
| 6 | fewer than 50% | >50% and <=66% |

### Threshold curve — constant `run-20260906T050515Z-constant` rep-1 (illustrative)

| Threshold (ms) | Fixed — faster than | HPA — faster than |
|---------------:|--------------------|--------------------|
| 100 | 0% (Min Response Time > threshold) | fewer than 50% |
| 250 | fewer than 50% | fewer than 50% |
| **500** | fewer than 50% | >66% and <=75% |
| 1000 | >80% and <=90% | >90% and <=95% |
| 2500 | >99% and <=99.9% | >99.9% and <=99.99% |
| 5000 | 100% (100% column <= threshold) | 100% (100% column <= threshold) |

### Error budget at 500 ms (SLO target 99%, allowed miss 1%)

See [headline finding](#slo-and-error-budget-calibrated-runs) above: every rep misses the target; brackets below are the supporting detail. Example hybrid rep-1 (either arm): miss **>= 50%** → budget consumed **>= 5000%** of the 30-day allowance; equivalent constant-miss exhaust **<= 0.60 days**. Example hybrid rep-2 HPA: miss **>= 34% and < 50%** → budget **>= 3400% and < 5000%**; exhaust **0.60–0.88 days**.

**Formula (if miss rate were constant):** `exhaust_days = 30 × (1 − SLO_target) / observed_miss_rate`. Brackets propagate; do not interpolate a point estimate.

**Multi-window burn-rate alerting does not apply.** Constants such as **14.4×** are defined for a **99.9% SLO over a 30-day compliance window** (example: `14.4 = 0.02 / (1h / 720h)`). An 18-minute benchmark has **no compliance period**, so no burn-rate tier is meaningful. **No alert rules are committed.**

### Scaling events (`replica_series_*.csv`, median across reps)

A scaling event is `spec_replicas[i] != spec_replicas[i−1]`. **replica-delta** is the sum of `|Δspec|` over the series.

| Shape | Arm | scale-ups | scale-downs | total events | replica-delta |
|-------|-----|----------:|------------:|-------------:|--------------:|
| hybrid (n=6) | fixed | 0 | 0 | 0 | 0 |
| hybrid (n=6) | HPA | 3 | 2 | 6 | 14 |
| constant (n=3) | fixed | 0 | 0 | 0 | 0 |
| constant (n=3) | HPA | 5 | 1 | 6 | 9 |

Source: `analysis/scaling_events.py` on `results/runs/`.

### Saturation signals

- **Indirect (collected):** `cpu_utilization_pct` and client/Prometheus p99 latency are present in the metrics and Locust artifacts. No new saturation score is derived in this pass.
- **Direct (pending Phase 5):** `active_requests` is empty in every existing run; see [`active_requests`](#active_requests-in-flight-saturation-gauge).

## What is not being claimed

- **HPA `behavior:` vs stock Kubernetes (`k8s/hpa.yaml`).** This manifest sets `scaleDown.stabilizationWindowSeconds: 60` (Kubernetes default **300**) and `scaleUp` policy `periodSeconds: 30` (Kubernetes default **15**). Direction only: this HPA scales up more slowly and sheds pods sooner than stock Kubernetes. No effect on cost or latency from these settings has been measured.
- **Prometheus `rps` and `error_rate` coverage.** Both are derived from `app_requests_total` in `analysis/collect_metrics.py`. `GET /` never increments that counter (`app/main.py`), so roughly **20%** of offered Locust traffic is invisible to both series.
- **`active_requests` in existing runs.** The `active_requests` column is empty in every run that exists. **Two causes:** Prometheus `--storage.tsdb.retention.time=2h` expired hybrid and constant samples before the backfill (~21:52 on 2026-09-06; only data after ~19:52 remained); `reset_prometheus_deployment` destroyed the flash remainder. Retention was never revisited. The limitation bullets under [`active_requests`](#active_requests-in-flight-saturation-gauge) apply to **future** runs only.
- **Load shapes.** All existing benchmark runs used synthetic phased shapes in `locust/locustfile.py` (`hybrid`, `constant`, `flash`). The measurements are real; those traffic patterns were invented. **New runs** should use trace-derived shapes (`wc98_*`, `rr_periodic`); synthetic locustfiles remain byte-identical for published-run replay only.
- **Latency SLI on existing runs.** Approximate only (Locust grid brackets on `/cpu`); exact count-based SLI requires Phase 5 bucket columns — not stored today.

## Superseded — run-20260904T230444Z

This was the published headline. It is not a fair comparison: HPA ran at `minReplicas=1` while the fixed arm was declared at 3, so the HPA arm started at one third the capacity. It is superseded by the calibrated minReplicas=3 runs (hybrid n=6, constant n=3, flash n=2 PARTIAL). The table and narrative below are preserved verbatim.

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

Coverage numbers in this section belong to superseded `run-20260904T230444Z`, not the calibrated results.

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

- **HPA `behavior:` vs stock Kubernetes (`k8s/hpa.yaml`).** `scaleDown.stabilizationWindowSeconds: 60` vs Kubernetes default **300**; `scaleUp` policy `periodSeconds: 30` vs default **15**. Direction only: this HPA scales up more slowly and sheds pods sooner than stock Kubernetes. No measured effect on cost or latency is claimed.
- **Synthetic load shapes.** Published runs through Phase A used phased shapes in `locust/locustfile.py` (`hybrid`, `constant`, `flash`). The measurements are real; those traffic patterns were invented. **`locust/locustfile.py` and `locustfile_{constant,flash}.py` are frozen** for published-run replay; do not change them. **New runs** use trace-derived shapes (`wc98_*`, `rr_periodic`); see [Trace-derived load shapes (Phase 4)](#trace-derived-load-shapes-phase-4).
- **Sub-plateau arrival burstiness is not reproduced.** Locust uses a closed-loop user model (`wait_time = between(1, 3)` per user). The superposition of N independent renewal processes is approximately Poisson (Palm-Khintchine), so traffic delivered to pods within each plateau is near-Poisson regardless of the source trace's fine-grained statistics. Conclusions about autoscaler behaviour under **bursty arrivals** apply to the **30 s envelope timescale and above**, not to fine-grained arrival burstiness in the original traces. Reproducing source arrival statistics would require an open-loop request scheduler (deferred — different load generator, not a Locust parameter change).
- **Application change after run-20260904T230444Z:** `/cpu` was `async def` (CPU work on the event loop, blocking `/health` under load). It is now sync `def` (Starlette threadpool dispatch). **Future runs are not comparable to run-20260904T230444Z** — the application under test has changed.
- **Starlette threadpool (40 tokens, unchanged):** At 200m CPU a pod does roughly 2 req/s; running 40 `/cpu` requests concurrently does not add throughput — Python's GIL serialises bytecode execution. The purpose of the `/cpu` handler change is **probe availability**, not throughput. Expect per-request latency to get **worse**, not better, under saturation. **Do not raise the AnyIO limiter from 40.** `app_active_requests` increments inside the `/cpu` handler after a token is acquired, so **40 is the saturation ceiling** for that series (queueing for a token is invisible).
- **Phase 5 app image (`FIRST_REQUEST_SERVED`):** Phase 5 adds one stderr log line on the first non-`/health`/`/metrics` request per process (middleware; not in `compute_primes` or the `/cpu` body). That is a different application image than every prior run. Prior GKE `image_tag`: **MISSING** (no committed `results/gke-deploy-manifest.json` in git). Phase 5 `image_tag`: **MISSING** until `scripts/deploy_gke.sh` writes `results/gke-deploy-manifest.json` at deploy. After that deploy, quote both tags from those artifacts; do not guess.
- **`psutil.cpu_percent(interval=None)` / `cpu_utilization_pct`:** The Prometheus column `cpu_utilization_pct` is sourced from `avg(app_cpu_usage_percent{experiment=...})`. The gauge is set by `psutil.cpu_percent(interval=None)` with **no process argument**, which returns **system-wide CPU**. Inside a container without lxcfs — GKE does not mount it — `/proc/stat` reports the **host node's** statistics. The values in published CSVs and the **CPU Util (%)** row in statistical summaries are therefore **node utilisation**, not pod utilisation. The measurements are real; the label is wrong. This is **not** the HPA resource metric (utilisation against the 500m request via metrics-server). Do not delete or recompute existing published values — they are correct measurements of a different quantity. The P1 capacity probe uses metrics-server (`kubectl top pods`) for pod-scoped CPU; see [`scripts/run_capacity_probe.sh`](scripts/run_capacity_probe.sh).
- **`psutil.cpu_percent(interval=None)` (noise):** Returns CPU since the previous call. It is invoked from `/` and `/fail` on the event loop; higher `/cpu` concurrency may make this gauge noisier (documented only; not fixed).
- **`check_event_loop_not_blocked` load generator:** The kind smoke check drives `/cpu` load from a Python harness run **inside** the application container via `kubectl exec`. That harness shares the pod's cgroup (200m CPU, 256Mi memory — same limits as the app) and its allocations count against the pod budget. Measured `/health` latency under this check is therefore a **conservative upper bound**, not a faithful reproduction of external load at equivalent request rate (where the app would have the full cgroup budget). Tier 2 backlog: drive load from outside the pod for a more faithful probe-availability test.
- **Probe values are conservative, not measured optima:** Kind in-container load has run-to-run `/health` variance far larger than between-config differences (five repeat runs at identical config and 15 threads/pod: `/health` max range **715–3992 ms**, a **5.6×** spread; see `PROGRESS.md` readiness-repeat-control). The readiness sweep (99faf65) is **withdrawn** as uninformative — between-config spread was 593–918 ms, roughly five times smaller than the noise. A1's **590.9 ms** and A2 probe values derived from it are single samples from that distribution. **GKE with 80 external Locust users** is the measurement of record for probe behavior.
- **Readiness probe (current):** `timeoutSeconds: 1`, `failureThreshold: 6`, `periodSeconds: 5` — a conservative choice against symmetric-overload EndpointSlice cascade (six consecutive failures ≈ 30s sustained unresponsiveness before removal), not a kind-measured optimum. `startupProbe` and `livenessProbe` unchanged from A2.
- **Load generator location:** Locust runs on the operator's laptop in California; load reaches `us-central1` over the public internet. Client RTT and uplink capacity are included in **client-observed** response time (Locust). Prometheus service time measures in-handler compute only after the request is accepted.
- **Comparison validity:** Both arms are affected identically (same client, same region path, same LoadBalancer topology), so fixed-vs-HPA comparisons are valid. Absolute latency numbers are **not** datacenter-internal measurements.
- **Tier 2 deferral:** Running Locust in-cluster (same region as the cluster) is deferred to Tier 2 to remove client-path variance from absolute latency.
- **Saturation.** Indirect signals (`cpu_utilization_pct`, p99 latency) are collected. Direct in-flight saturation (`active_requests`) is pending Phase 5 — the column is empty in every existing run.

### Cluster sizing constraint

- The cluster is **3 × e2-standard-4** (12 vCPU global), not the 5 × e2-standard-8 nodes originally planned.
- The 5-node figure was taken from SLO-Scaler (arXiv:2608.18390), which evaluated DeathStarBench, a twelve-microservice application. This project runs a single FastAPI service, so the node count was borrowed rather than derived.
- The binding constraint is a project **CPUS_ALL_REGIONS** quota of **12**. Google declined an increase on usage-history grounds despite a paid billing account ($109 cost fully offset by $109 in credits; billed spend $0.00). The eligibility check reads billed spend, not credit consumption, so the gate cannot be cleared by asking again.
- **~10,055m usable CPU** after measured allocatable (11,760m on 3 × e2-standard-4) minus system pod requests (~1,605m) and Prometheus (~100m).
- **`maxReplicas` is 12**, not the 20 originally planned, because twenty HPA replicas plus four fixed at 500m each would demand 12,000m against ~10,055m usable — leaving ~55m margin (0.5%) that ignores concurrent arms, per-node bin-packing, and the scheduler needing a full 500m slot on one node. The HPA arm would hit Pending pods in the mid-teens; scale-out would be capped by scheduling, not the autoscaler — a silent confound.
- **12 × 500m HPA plus 4 × 500m fixed = 8,000m** against ~10,055m usable (~20% headroom). Every replica in the **4–12** range is genuinely schedulable. The scaling range is **4 to 12 (3×)**. SLO-Scaler (arXiv:2608.18390) reports average replica counts of **3.2–4.8** across workloads — comparable in multiplier, not in absolute pod count.
- Do not present `maxReplicas: 12` as the intended value. State the measurement and the consequence.
- Nodes are **e2-standard-4**, not e2-standard-8, because of the global quota above. Regional CPUS (us-central1) is not the binding limit.
- The Phase 3 runner VM (`hpa-bench-runner`, e2-standard-4, 4 vCPU) **cannot run concurrently** with the cluster: 3 × 4 + 4 = 16 vCPU exceeds the global limit of 12. The runner VM must be **stopped** before cluster creation; preflight fails with `RUNNER_VM_MUST_BE_STOPPED` if it is running.
- **Load runs from the Mac**, not the runner VM. Locust reaches `us-central1` over the public internet, so roughly **50 ms of internet RTT** is included in every client-observed latency figure. The offset applies identically to all three arms, so comparisons remain valid; absolute latencies are not datacenter-internal.
- The consequence is throughput, not design. At roughly **0.1 core-seconds per request**, sustained load caps near **105 RPS** at full saturation on ~10.5 usable cores, so a realistic target is **60–80 RPS**. That is **below** SLO-Scaler's 120–280 RPS range — state that plainly; do not claim parity.
- Do not describe this as the intended configuration. State the constraint and its effect.
- **Estimated Phase 5 spend (not invoiced):** ~25 h cluster at 3× `e2-standard-4` (~$0.40/hr on-demand list) plus two LoadBalancers → ballpark **~$11–13**; runner VM stopped during the run. See Phase 5 plan cost section.
- **P1 capacity probe stop rule:** unchanged by node count. The probe stops at the first step where median pod CPU is ≥ 80% of the 1000m limit or RPS per user falls versus the previous step (`CAPACITY_PROBE_TIMEOUT` if neither occurs within 12 minutes). That rule is pod-local on the fixed arm (4 replicas). Saturation arrives at a lower Locust user count than larger clusters would have needed; the stop rule still fires on that step.
- **`SHAPE_MEAN_USERS`:** Derived by the P1 capacity probe (`bash scripts/run_capacity_probe.sh --env-file .env`), not assumed from the A7 Little's-law default of 45. The integer is written to `results/capacity_probe/derivation.json` after a live probe run. **No value is recorded here until that artifact exists.**

## Trace-derived load shapes (Phase 4)

Five trace-derived shapes replace synthetic `hybrid` / `constant` / `flash` for **new** benchmark runs. Selection rule: [`docs/SHAPE_SELECTION.md`](docs/SHAPE_SELECTION.md) (`shape_selection_rule_v2`). Per-shape provenance JSON: `docs/shape_provenance/`. Locustfiles: `locust/locustfile_wc98_*.py`, `locust/locustfile_rr_periodic.py`.

**Amplitude is a deployment parameter.** Each locustfile stores **unit-mean plateaus** only (`UNIT_MEAN_PLATEAUS`, time-weighted mean = 1.0). Absolute user counts come from `SHAPE_MEAN_USERS` (default **45**). Phase 5 may raise this for larger clusters; integer rounding of `round(unit_mean × SHAPE_MEAN_USERS)` is the only amplitude distortion.

**Spawn rate scales with amplitude.** `_spawn_rate()` preserves the steepest plateau-to-plateau transition duration measured at `SHAPE_MEAN_USERS=45` (see `scripts/lib/shape_spawn.py`). Without this, flash onset would stretch linearly with the multiplier (observed **5 s** at ×400 before the fix vs **3 s** at ×45 and ×400 after).

**Hurst:** only **`hurst_native`** is published — R/S on the **source trace window** at selection time. It describes selection context, **not** the Locust-delivered load. No `hurst_plateau` or `hurst_scaled` (plateau thinning destroys sub-30 s structure; scaled replay is not what Locust generates). See `hurst_note` in each provenance file.

**RetailRocket scope:** `rr_periodic` only. RR constant had zero eligible windows under v2; see `retailrocket_limitation` in `docs/shape_provenance/rr_periodic.json`.

**Local shape-curve validation** (`scripts/smoke_test.sh --check shape-curve`): all five shapes passed `SHAPE_ACHIEVES_TARGET` at `SHAPE_MEAN_USERS=45` (`max_abs_err_users=0`). `wc98_flash` was additionally validated at **400** after spawn-rate scaling. Observed steepest transition seconds are recorded per multiplier in each provenance JSON (`shape_curve_steepest_transition_observed_sec`); **multiplier-independence is not claimed beyond `shape_mean_users_validated`.** Checker assertion `SHAPE_SUDDENNESS_PRESERVED` requires observed transition within **3 s** of `steepest_spawn_transition_sec` (reference at ×45).

| Shape | Dataset | Timezone | `peak_to_mean` | `dilation_factor` | `hurst_native` | `fastest_feature_duration_sec` | `steepest_spawn_transition_sec` | HPA no-scale | Validated `SHAPE_MEAN_USERS` |
|-------|---------|----------|---------------:|------------------:|---------------:|---------------------------------:|----------------------------------:|:--------------:|-----------------------------:|
| `wc98_flash` | WorldCup98 | France +0200 | 2.015 | 1 | 0.885 | 32.1 | 2.1 | abort | 45, 400 |
| `wc98_ramp` | WorldCup98 | France +0200 | 1.521 | 1 | 0.955 | 30.7 | 0.7 | abort | 45 |
| `wc98_constant` | WorldCup98 | France +0200 | 1.051 | 1 | 0.570 | 30.3 | 0.3 | **warn** | 45 |
| `wc98_periodic` | WorldCup98 | France +0200 | 1.588 | **80** | 0.884 | 1.675 | 1.3 | abort | 45 |
| `rr_periodic` | RetailRocket | UTC | 1.509 | **80** | 0.780 | 2.175 | 1.8 | abort | 45 |

Periodic shapes dilate a **24 h** source window (`source_window_sec=86400`) into **18 min** playback (`playback_sec=1080`, factor **80**). WorldCup98 windows are aligned to **France fixed +0200** local midnight (`timezone=france_fixed_plus0200`).

### Baseline calibration (Tier 2 Phase A, A8)

Calibrated outcomes are published in [Calibrated results](#calibrated-results-minreplicas3-both-arms) (hybrid n=6, constant n=3, flash n=2 PARTIAL).

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

**Every shape is a step function, not a linear ramp.** `tick()` returns a target and Locust rate-limits spawning toward it; it does not interpolate. `locustfile.py`'s docstring reads "Ramp-up (0–3 min): 1 → 20 users, spawn rate 2/s", but `PhasedLoadShape.tick()` returns `(20, 2)` for the whole `0 < t ≤ 180` window, so the shape jumps to 20 and merely takes 20/2 = 10 s to spawn them. Time-weighted means must therefore use **step levels**, not segment midpoints.

| Shape | Curve (step levels) | Analytic step mean | Measured mean (Step 6a) |
|---|---|---:|---:|
| hybrid | 20 → 80 → 60 → 5 | **47.5** — (180×20 + 180×80 + 540×60 + 180×5)/1080 | **47.9916** |
| constant | 45 flat, ±10% seeded noise | **45.4167** — 36 seeded plateaus (`NOISE_SEED=1729`, band 41–49) | **45.3876** |
| flash | 30 → 90 → 30 | **40.0** — (420×30 + 180×90 + 480×30)/1080 | **40.1124** |

Measured means are from the authoritative **sequential** Step 6a run (`check_shape_achieves_target` on `/tmp/shape6a/*_stats_history.csv`; parallel runs on one host were discarded because CPU contention distorts spawn timing). All three shapes passed `SHAPE_ACHIEVES_TARGET` with `max_abs_err_users=0`. Flash 30→90 transition completed in **1 s** (`spawn_rate=60`); hybrid 20→80 in **2 s**.

**Constant anchoring error.** `constant` was set to **45** users because hybrid was estimated at **45.5**, computed as (180×10.5 + 180×50 + 540×60 + 180×32.5)/1080 by averaging each stage's endpoints — which assumes `tick()` ramps linearly between levels. It does not; `tick()` is a step function. The step-correct hybrid analytic mean is **47.5**, and the measured hybrid mean is **47.9916** (~48). `constant` at **45.3876** therefore runs **5.4% lighter** than hybrid ((47.9916 − 45.3876) ÷ 47.9916), not the ~0.2% originally claimed. The shapes were **not** rebuilt: the difference is small, and each shape's mean is reported precisely so the mismatch stays visible rather than hidden. `flash` at 40.1124 sits **16.4%** below hybrid.

**Reconciling analytic, full-history, and settled means.** Three bases apply; they must not be mixed in one sentence.

1. **Analytic step integral** — plateau levels × stage duration ÷ 1080 s; instant attainment at each boundary.
2. **Full-history sample mean** — trapezoidal integration of achieved `User Count` over all ~1 Hz `locust_*_stats_history.csv` samples spanning 1080 s, including spawn ramps.
3. **`SHAPE_TIME_WEIGHTED_MEAN_USERS` (headline measured value)** — same integration as (2) but **excluding** samples in 12 s windows after t=0 and each target change (`check_shape_achieves_target` default). Those windows hold below-target ramp samples; dropping them shifts weight toward plateaus.

| Shape | Analytic | Full-history | Settled (headline) | Settled − analytic |
|---|---:|---:|---:|---:|
| hybrid | 47.5 | 47.6389 | **47.9916** | +1.03% |
| constant | 45.4167 | 45.3102 | **45.3876** | −0.06% |
| flash | 40.0 | 39.9722 | **40.1124** | +0.28% |

**Hybrid 47.5 vs 47.9916.** Full-history (47.6389) is within **0.29%** of analytic: the t=0 ramp (0→20 at 2/s, ~10 s sub-target) and brief catch-up ramps at stage edges (20→80 in 3 s, 60→5 in 11 s) pull the all-sample mean up slightly. Settled-sample mean is **0.35 pp higher** than full-history because the 12 s exclusion removes those below-target windows (34 transitions including t=0); with `max_abs_err_users=0` the excluded samples are systematically low, not random noise.

**Flash 40.0 vs 40.1124.** Full-history (39.9722) is within **0.07%** of analytic. Settled mean sits **0.14 pp above** 40.0 for the same mechanism: excluding the 30→90 ramp window (12 s below 90 users) and the 90→30 decay window raises the settled weight above the step integral. The 30→90 step itself completed in **1 s**, confirming `spawn_rate=60` behaved as designed.

**Locust ignores `--run-time` when a `LoadTestShape` is present** (it warns: "--run-time, --users or --spawn-rate have no impact on LoadShapes unless the shape class explicitly reads them"). Each shape therefore terminates itself by returning `None` from `tick()` at 1080 s, and the harness `--run-time 18m` plus its 60 s wall-clock guard remain the outer bound rather than the mechanism. The two agree by construction; if a shape's internal duration is ever changed, `--run-time` will not correct it.

#### Prediction recorded before the calibrated runs

A7's HPA arm averaged **8.16** ready replicas, recomputed from `results/runs/run-20260905T160157Z/rep-1/replica_series_hpa.csv`: pod-hours 2.4289 over a 1,071 s span, so 2.4289 × 3600 ÷ 1071 = 8.16 time-weighted (simple mean over the 70 samples is 8.07). Hybrid's measured time-weighted mean is **47.9916** users; `constant` at **45.3876** is ~5.4% lighter, not matched volume — so **expect roughly 6–8 replicas on constant** (same order as A7 hybrid-level load, not a like-for-like match).

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

### `active_requests` (in-flight saturation gauge)

The `active_requests` column is **empty in every existing run**. **Two causes:** `--storage.tsdb.retention.time=2h` expired hybrid and constant samples before the backfill (~21:52 on 2026-09-06); `reset_prometheus_deployment` destroyed the flash remainder. Retention was never revisited. The bullets below describe how the column is collected and how it should be read in **future** runs, not values from published CSVs.

Collected as `sum(app_active_requests{experiment="<mode>"})` and written to the `active_requests` CSV column (backfilled on GKE runs from 2026-09-05 onward while the live cluster TSDB was still available).

**Limitations — read before interpreting this column in future runs:**

1. **Partial traffic coverage.** `ACTIVE_REQUESTS` is incremented only in the `/cpu` handler (`app/main.py`). `GET /` and `GET /fail` never touch the gauge, so it reflects roughly **80%** of offered Locust traffic (the `@task(4)` `/cpu` share vs `@task(1)` `/`).
2. **Hard cap at 40.** `/cpu` is a sync `def` handler; Starlette runs it in AnyIO's default thread limiter (**40** tokens). `ACTIVE_REQUESTS.inc()` is inside the handler body, so requests queued waiting for a token are **not** counted. The series cannot show queueing beyond 40 in-flight `/cpu` calls per pod.
3. **Scale-down staleness exclusion.** Prometheus retains a target's last scraped gauge value for up to **5 minutes** after the pod disappears (default lookback delta). During HPA scale-down, `sum(app_active_requests)` can therefore include terminated pods' last non-zero in-flight counts. Collection and backfill write **`MISSING`** (not the inflated sum) for any row whose `ready_replicas` changed within the preceding **300 s**, as recorded in `replica_series_<arm>.csv`. Rows with `ready_replicas == 0` remain `TARGET_UNAVAILABLE` per the usual availability contract.

## Locust failure semantics

- **`Unexpected status 0`:** In Locust logs/CSV this means the HTTP client received no response (connection error, timeout, or reset) — **not** an HTTP 5xx.
- **Exit code vs measurement:** Locust exits non-zero when any sample fails unless `--exit-code-on-error 0` is set. Request failures are the measurement under burst load; the harness treats a completed load shape with valid stats as success.
- **Prometheus `error_rate` vs Locust failure rate:** Prometheus `error_rate` measures **server-observed** non-200 responses (`app_requests_total{status_code!="200"}`). Locust measures **client-observed** outcomes including connection failures and timeouts. When the app is unreachable or overloaded, the client sees failures the server never records. **Locust is authoritative for the published failure rate.**

**Worked example (superseded run `run-20260904T230444Z`):**
- HPA arm: Locust **63 / 20,820 (0.30%)**; Prometheus `error_rate` **non_zero=0** for the run — consistent (connection-level failures).
- Fixed arm: Locust **1,230 / 10,193 (12.07%)**; Prometheus `error_rate` **~0.0** — same mechanism during baseline collapse.

## Latency — two metrics, never merged

**Superseded run (`run-20260904T230444Z`).** The percentile tables below are not the calibrated results. See [Calibrated results](#calibrated-results-minreplicas3-both-arms) for hybrid and constant medians.

Published latency is **two separate metrics**. Do not compare or average them.

### Response time (client-observed, Locust)

**Authority:** Locust `locust_*_stats.csv` Aggregated row. Includes queueing, connection setup, TLS, and failures — the full wall-clock time from the load generator's perspective.

**Superseded run only** — table values are from `run-20260904T230444Z` rep-1.

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

**Superseded run only** — table values are from `run-20260904T230444Z` rep-1.

| Arm | Mean p50 (ms) | Mean p95 (ms) | Mean p99 (ms) | Populated rows | Source |
|-----|-------------:|--------------:|--------------:|---------------:|--------|
| **Fixed** | 133 | 239 | 253 | 33/57 assessable serving rows | `fixed_metrics.csv` |
| **HPA** | 148 | 239 | 251 | full window | `hpa_metrics.csv` |

**Documented limitations (not fixed in this pass):**

- **`GET /` is not instrumented.** `app/main.py` does not observe `REQUEST_LATENCY` or `REQUEST_COUNT` on the health-check route — **20% of offered traffic** is invisible server-side. This affects `rps` and `error_rate` (see [`What is not being claimed`](#what-is-not-being-claimed)) as well as service-time percentiles.
- **Histogram bucket resolution.** Buckets `[..., 0.1, 0.25, 0.5, ...]` (`app/main.py:38`) place the published p95 of ~237 ms inside the single 0.1–0.25 s bucket, making it a linear interpolation within one bucket rather than a resolved measurement.

## Scale-up lag (HPA arm)

**Superseded run (`run-20260904T230444Z`).** The analysis below is specific to that run's HPA arm, not the calibrated results.

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

## Derived metrics — calibrated runs

Medians and p-values below match [Calibrated results](#calibrated-results-minreplicas3-both-arms). No throughput ratio — request counts for these runs were not published in this pass. No flash derived metrics.

### hybrid — `run-20260905T220046Z-hybrid` (n=6)

| Metric | Fixed (median) | HPA (median) | p (two-sided) |
|--------|---------------:|-------------:|--------------:|
| failure_rate | 0.000124 | 0.000163 | 0.562500 (not significant) |
| pod_hours | 0.890417 | 2.493750 | 0.031250 |
| cost_per_1k | 0.000147246 | 0.000339106 | 0.031250 |

### constant — `run-20260906T050515Z-constant` (n=3)

| Metric | Fixed (median) | HPA (median) | p (two-sided) |
|--------|---------------:|-------------:|--------------:|
| failure_rate | 0 | 0.00010008 | 0.250000 |
| pod_hours | 0.891667 | 2.723060 | 0.250000 |
| cost_per_1k | 0.000125359 | 0.000363458 | 0.250000 |

## Derived metrics — run-20260904T230444Z

**Superseded run.** The derived metrics below are for `run-20260904T230444Z` only; they are not the calibrated results.

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
- **Not applicable.** Multi-window burn-rate tiers (e.g. **14.4×** at 1h/5m for a **99.9%** SLO over **30 days**) assume a compliance window. An 18-minute benchmark has none. See [SLO and error budget (calibrated runs)](#slo-and-error-budget-calibrated-runs). No alert rules are committed.

### Run-scoped error budget consumption (%) — Locust failure rate, not latency SLO
- **Fixed:** 12.07% of requests failed (Locust) — 1230 / 10193
- **HPA:** 0.30% of requests failed (Locust) — 63 / 20820
- **Source:** `locust_*_stats.csv` Aggregated rows

## Figures — run-20260904T230444Z (superseded)

The six PNGs below are from rep-1 of `run-20260904T230444Z`. **Aggregate figures for the calibrated runs are pending** — per-rep charts are not published under cross-repetition medians.

Generated with `--allow-partial-coverage` for the Prometheus service-time figure only (see [Data completeness](#figure-generation-partial-coverage-disclosure)). Fixed arm PARTIAL on Prometheus metrics; HPA arm complete. Client-observed figures are Locust-sourced.

- `docs/figures/run-20260904T230444Z/latency_client_run_level.png` — client-observed p50/p95/p99 (superseded run)
- `docs/figures/run-20260904T230444Z/latency_client_window.png` — client-observed, 10-second sliding window
- `docs/figures/run-20260904T230444Z/latency_comparison.png` — service time (in-handler, Prometheus)
- `docs/figures/run-20260904T230444Z/throughput_comparison.png`
- `docs/figures/run-20260904T230444Z/cpu_replicas.png`
- `docs/figures/run-20260904T230444Z/cost_performance.png`

Original run artifacts: `results/runs/run-20260904T230444Z/rep-1/figures/`.
