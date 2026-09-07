# Postmortem: unfair HPA vs fixed comparison from unequal starting capacity

**Authors:** Sriram Madduri (sole maintainer)  
**Status:** resolved  
**Publication:** initial README claim in commit `951b89e` (2026-05-12)  
**Detection:** Detected during a verification pass roughly five months after the initial publication. Nothing alerted; the check was manual and self-initiated.

## Summary

The repository initially published an HPA-vs-fixed comparison that treated **51.7% failure (fixed) vs 0.97% (HPA)** from `sample_data/` as a fair headline (`951b89e` README). A later GKE run (`run-20260904T230444Z`) repeated the pattern at **12.07% vs 0.30%** and was promoted to the README front page. In both cases the HPA arm ran at **`minReplicas: 1`** while the fixed arm was declared at **3 replicas**, so the HPA arm started at one third the capacity and incurred scale-up queueing the fixed arm never paid. The harness did not assert equal starting capacity, and **`HPA_NEVER_SCALED` only verified that peak `spec_replicas` exceeded `minReplicas`** — so scaling from 1→10 still passed the guard while the comparison remained unfair.

Resolution: `k8s/hpa.yaml` now sets **`minReplicas: 3`**; calibrated runs (hybrid n=6, constant n=3) compare both arms from equal floor capacity; the superseded run and README table are preserved with an explicit fairness disclaimer; SLO and error-budget analysis is in [`RESULTS.md`](RESULTS.md#slo-and-error-budget-calibrated-runs).

## Impact

Readers of the README and RESULTS treated the published failure-rate gap as evidence that HPA improved reliability on the same workload under the same starting conditions. The gap was real in the Locust artifacts, but the arms were not equally provisioned at run start. Anyone reproducing the benchmark from the old manifests would inherit the same confound.

Downstream effects in this repo:

- Headline figures and PNGs under `docs/figures/run-20260904T230444Z/` reflected the unequal baseline until demoted to a superseded section.
- `run-20260905T160157Z` is documented as non-comparable for the same reason (`replica_series_hpa.csv` minimum `spec_replicas` of **1**).

## Root causes

1. **Unequal declared floor capacity.** HPA `minReplicas: 1` vs fixed deployment at 3. Documented in [`RESULTS.md`](RESULTS.md#superseded--run-20260904t230444z) and [`k8s/hpa.yaml`](k8s/hpa.yaml) (now corrected to 3).

2. **No assertion that both arms start at equal declared capacity.** The runner records declared replicas for the fixed arm but did not require the HPA floor to match before treating results as comparable.

3. **`HPA_NEVER_SCALED` guard is necessary but insufficient for fairness.** Collection aborts only when peak in-window `spec_replicas` never **exceeds** `minReplicas`:

```435:442:analysis/collect_metrics.py
    if mode == "hpa" and min_replicas is not None:
        if peak_spec <= min_replicas:
            if hpa_no_scale_policy == HPA_NO_SCALE_ABORT:
                msg = (
                    f"HPA_NEVER_SCALED peak_observed={peak_spec} minReplicas={min_replicas}"
                )
                print(msg, file=sys.stderr)
                raise RuntimeError(msg)
```

   With `minReplicas=1`, peak **10** passes (`10 > 1`) even though the fixed arm held **3** from the first sample. The guard proves scaling occurred, not that the arms began equally sized.

4. **Headline used Locust Aggregated failure rates without disclosing the replica confound** until the A8 write-up and Phase 1 calibrated publication.

## Trigger

Detected during a verification pass roughly five months after the initial publication. Nothing alerted; the check was manual and self-initiated. The maintainer re-read replica series and manifest defaults while preparing calibrated reruns.

## Resolution

| Step | When | What |
|------|------|------|
| Manifest fix | 2026-09-05 (`2439a8d`) | `minReplicas: 3` in `k8s/hpa.yaml` |
| Write-up | 2026-09-05 (`d3eba8f`) | A8 baseline calibration and non-comparable run called out in RESULTS |
| Calibrated runs | 2026-09-05–06 | `run-20260905T220046Z-hybrid` (n=6), `run-20260906T050515Z-constant` (n=3) |
| Publication fix | 2026-09-07 (`43f9d19`–`07a7236`) | Calibrated tables promoted; superseded run demoted; README updated |
| SLO framing | 2026-09-07 | Approximate `/cpu` SLI and error budget in RESULTS; policy in `docs/error-budget-policy.md` (Step 5) |

Calibrated comparison shows HPA with lower client p50 medians but **neither arm meets a 99% / 500 ms `/cpu` SLO** in any repetition — see [`RESULTS.md`](RESULTS.md#slo-and-error-budget-calibrated-runs).

## Detection

- **How:** manual re-verification of replica series and HPA manifest defaults; no monitor or CI rule fired.
- **When:** roughly five months after initial publication (`951b89e`, 2026-05-12); no precise detection timestamp is recorded in the repo.
- **Lag:** the unequal baseline was present from the first published headline through `run-20260904T230444Z` until `minReplicas` was raised and calibrated runs completed.

## Action items

| Action | Owner | Status |
|--------|-------|--------|
| Assert both arms start at equal declared capacity before recording a comparison as calibrated | Sriram Madduri | open |
| Raise HPA `minReplicas` to match fixed floor (3) | Sriram Madduri | done (`2439a8d`) |
| Publish calibrated results with superseded section preserved | Sriram Madduri | done (Phase 1) |
| Document approximate `/cpu` SLI and error budget | Sriram Madduri | done (`RESULTS.md`) |
| Phase 5: store raw histogram bucket counts for exact SLI | Sriram Madduri | planned (`docs/phase5-bucket-schema.md`) |
| Add a Prometheus PersistentVolumeClaim before Phase 5 | Sriram Madduri | open |

## Lessons learned

### What went well

- Locust `*_stats.csv` files were retained on disk; client percentiles and per-endpoint rows were recoverable without re-running the cluster (`a0-1`, 2026-09-04).
- `replica_series_<arm>.csv` sampling made the 1-vs-3 floor visible once someone looked.
- Superseded artifacts were kept verbatim rather than deleted, so the confound remains auditable.

### What went wrong

- **Detection lag:** no automated check compared HPA `minReplicas` to the fixed arm's declared count before publishing.
- **Headline before calibration:** README promoted failure-rate deltas before equal-capacity runs existed.
- **Guard semantics misunderstood:** `HPA_NEVER_SCALED` was treated as sufficient proof of a fair scaling experiment.
- **SLO placeholder:** latency tail compliance was not defined until Phase 2; Aggregated p50 masked `/cpu` tail behavior.

### Where we got lucky

- Locust percentile columns (`50%` … `100%`) were on disk the entire time, enabling retroactive approximate SLI brackets without a new benchmark run.
- Prometheus runs on **emptyDir** with **no PersistentVolumeClaim**, and its TSDB was wiped on **2026-09-06** before the `active_requests` backfill could run — losing the saturation metric for every completed run. Other Prometheus-derived columns survived only because collection runs **immediately after each arm** rather than retroactively. Had the pipeline depended on querying Prometheus later, every metric from every run would have been lost the same way, not just `active_requests`. That is luck, not design.

## Timeline

| Date | Event |
|------|-------|
| 2026-05-12 | `951b89e` — initial commit; README claims **51.7% vs 0.97%** from `sample_data/` |
| 2026-09-02 | `12b2f1c` — RESULTS scaffold, provenance, superseded sample_data |
| 2026-09-04 | `d02ee7c` — `run-20260904T230444Z` published; **12.07% vs 0.30%** headline |
| 2026-09-04 | `a0-1` — Locust percentile recovery verified on 230444Z artifacts |
| 2026-09-05 | `2439a8d` — `minReplicas: 3` in HPA manifest |
| 2026-09-05 | `d3eba8f` — A8 documents baseline calibration; `run-20260905T160157Z` marked non-comparable |
| 2026-09-05–06 | Calibrated hybrid and constant runs executed |
| 2026-09-07 | Phase 1 — calibrated RESULTS and README promotion; superseded section retained |
| ~2026-09 (qualitative) | Manual verification detects 1-vs-3 confound; no repo timestamp |
| 2026-09-07 | Phase 2 — SLO/error-budget section and this postmortem |

## Supporting information

- [`RESULTS.md`](RESULTS.md) — calibrated tables, superseded `run-20260904T230444Z`, SLO section
- [`DATA_PROVENANCE.md`](DATA_PROVENANCE.md) — artifact lineage
- [`k8s/hpa.yaml`](k8s/hpa.yaml) — current `minReplicas: 3`
- `results/runs/run-20260904T230444Z/rep-1/replica_series_hpa.csv` — `HPA_SCALE_FLOOR_CHECK peak=10 minReplicas=1`
- `results/runs/run-20260905T160157Z/rep-1/replica_series_hpa.csv` — minimum `spec_replicas` **1**
- `analysis/collect_metrics.py` — `HPA_NEVER_SCALED` guard
- `analysis/sli_locust_grid.py` — approximate `/cpu` SLI brackets
