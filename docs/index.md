# k8s-hpa-benchmark

Phase 5 measured Kubernetes CPU-target HPA against a fixed replica floor on trace-derived load. Three arms: `fixed`, `hpa_tuned`, `hpa_stock`. `SHAPE_MEAN_USERS=69`, `minReplicas=4`, `maxReplicas=12`.

Sources: [RESULTS.md](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/RESULTS.md), [repository](https://github.com/smadduri9/k8s-hpa-benchmark). Related: [POSTMORTEM.md](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/POSTMORTEM.md), [error-budget-policy.md](error-budget-policy.md), [SHAPE_SELECTION.md](SHAPE_SELECTION.md).

## Finding 1. Flash n=6 latency

`wc98_flash`, `results/runs/run-phase5-wc98_flash/`. Client p95 medians from Locust Aggregated `95%`, aggregated by `analysis/aggregate_runs.py`.

| Arm | client p95 (ms) | n |
|-----|----------------:|--:|
| fixed | 510 | 6 |
| hpa_tuned | 400 | 6 |
| hpa_stock | 380 | 6 |

Both HPA arms beat fixed at p=0.031250, the n=6 Wilcoxon floor, in 6 of 6 reps. hpa_tuned vs hpa_stock p=0.093750. Tuning the `behavior:` block made no detectable difference to latency. That is a null result. The project had previously only disclosed the confound.

Pod-hours: fixed 1.18556 (n=6), hpa_tuned 1.78361 (n=5), hpa_stock 2.23514 (n=6). Tuned n=5 because `rep-1/hpa_tuned/replica_series_hpa.csv` is a leaked 21-hour sampler (full-file pod-hours 96.64138888888888). Locust latency for that arm is intact.

## Finding 2. Workload coverage

Observed peak `spec_replicas` at `SHAPE_MEAN_USERS=69`.

| Shape | peak_to_mean | peak spec_replicas |
|-------|-------------:|--------------------|
| wc98_flash | 2.015377 | 10–12 |
| wc98_ramp | 1.520607 | 8, then 4–5 |
| wc98_periodic | 1.588318 | 5 |
| rr_periodic | 1.509042 | 5 |
| wc98_constant | 1.05071 | 4 |

Peak-to-mean ratio determines whether CPU-target HPA engages. Only flash-crowd bursts move it off its floor. Predicted in commit `514048c` before the n=1 runs. The prediction matched.

## Finding 3. Reproducibility

`wc98_ramp`, same shape and config. Locust Aggregated requests: 34283 (`rep-1/hpa_tuned`) vs 33835 (`rep-2/hpa_tuned`). Peak spec_replicas: 8 on the first cluster, 4–5 on the second. Warm-up CPU for the same 37-user hold: 154–240 millicores vs 73–166. GKE e2 machines span CPU generations. The draw is not selectable. A marginal shape can change behaviour between deployments of the same spec. Marginal-shape results are reported at n=1.

## Also recorded

Cold-start collection is dropped after five attempts with no usable rows. The collector was a passive observer. Benchmark Locust, replica, and metrics results are unaffected. Scaling from live `spec.replicas` (`declared_replicas=5`) is a known issue.

Phase 1 calibrated tables (synthetic shapes, minReplicas=3) stay in RESULTS.md, marked superseded.
