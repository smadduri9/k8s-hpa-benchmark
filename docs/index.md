# k8s-hpa-benchmark

Phase 5 measured Kubernetes CPU-target HPA against a fixed replica floor on trace-derived load. Three arms: `fixed`, `hpa_tuned`, `hpa_stock`. `SHAPE_MEAN_USERS=69`, `minReplicas=4`, `maxReplicas=12`.

Sources: [RESULTS.md](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/RESULTS.md), [repository](https://github.com/smadduri9/k8s-hpa-benchmark). Related: [POSTMORTEM.md](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/POSTMORTEM.md), [error-budget-policy.md](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/docs/error-budget-policy.md), [SHAPE_SELECTION.md](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/docs/SHAPE_SELECTION.md).

<!-- BEGIN V1.1 GENERATED FINDINGS -->

Evidence and offline commands: [v1.1 package](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/artifacts/v1.1/README.md). Authority: [verify.py](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/artifacts/v1.1/verify.py) and [headline.json](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/artifacts/v1.1/summary/headline.json); the historical general aggregator does not apply the publication exclusions.

Trace-derived WorldCup98 and RetailRocket load; three arms (`fixed`, `hpa_tuned`, `hpa_stock`). `SHAPE_MEAN_USERS=69` is a model-derived calibration parameter, not measured maximum capacity. Declared HPA floor: 4 replicas.

### Finding 1. Flash latency and ready-pod time

Client p95 medians: fixed **510 ms**, tuned **400 ms**, stock **380 ms**, across **6 paired repetitions**. Tuned p95 was lower than fixed in 6/6; stock was lower in 6/6.

Exact two-sided Wilcoxon: fixed/tuned **p=0.031250**, fixed/stock **p=0.031250**, tuned/stock **p=0.093750**. These are unadjusted tests. The tuned/stock comparison does not establish equivalence or a benefit from tuning. Arm order was not randomized.

Median ready-pod hours: fixed **1.18556 (n=6)**, tuned **1.78361 (n=5)**, stock **2.23514 (n=6)**. Ratios of unrounded medians are **+50.445173%** tuned/fixed and **+88.530928%** stock/fixed. Ready-pod time measures ready replicas integrated over sampled time; it does not measure consumed CPU or billing.

[Exclusions policy](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/artifacts/v1.1/exclusions.csv): tuned flash rep-1 retains its latency measurement but is excluded from ready-pod time because its replica series spans beyond the benchmark window. The contaminated file is preserved unchanged; its historical process origin is not independently evidenced. See [per-repetition results](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/artifacts/v1.1/summary/flash_repetitions.csv) and [paired tests](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/artifacts/v1.1/summary/statistical_tests.csv).

### Finding 2. Workload coverage

Observed in-window peak `spec_replicas`, listed in repetition order:

| Workload | Repetitions | Tuned HPA | Stock HPA |
|---|---:|---|---|
| `wc98_flash` | 6 | 12, 11, 11, 11, 10, 11 | 11, 12, 12, 11, 11, 11 |
| `wc98_ramp` | 2 | 8, 4 | 8, 5 |
| `wc98_constant` | 1 | 4 | 4 |
| `wc98_periodic` | 1 | 5 | 5 |
| `rr_periodic` | 1 | 5 | 5 |

Fixed-arm peaks were 4 throughout this completed scope. Flash, ramp and both periodic workloads showed scale-out above the floor in at least one retained arm/repetition. Constant did not. These observations do not establish peak-to-mean ratio as a sufficient predictor of HPA engagement. Source: [replica peaks and request counts](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/artifacts/v1.1/summary/replica_peaks.csv).

### Finding 3. Ramp run/deployment sensitivity

Two ramp repetitions produced similar measured request counts (34,283 and 33,835 in the tuned arm) but different scale-out: tuned peaked at 8 replicas in one repetition and 4 in the other. The retained evidence does not establish a hardware-level cause. This is evidence of run/deployment sensitivity, not a causal CPU-platform finding. Paired warm-up CPU vectors and hardware inventories are `MISSING`.

Flash has six completed repetitions, ramp two, and each other workload one. The originally defined three-repetition scope for the non-flash workloads was not completed. Incomplete ramp rep-3 is excluded. The non-flash observations are descriptive, not a workload ranking.

<!-- END V1.1 GENERATED FINDINGS -->

## Also recorded

No Phase 5 cold-start distribution is published. See the [measurement limitations](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/RESULTS.md#measurement-limitations). Historical write-ups remain accessible in the [results archive](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/docs/archive/SUPERSEDED_RESULTS.md).
