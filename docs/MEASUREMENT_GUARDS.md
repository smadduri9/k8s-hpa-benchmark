# Measurement guards

The [diagram](assets/figures/measurement_guards.svg) summarizes implemented collection checks and the publication path. A failed assertion blocks the affected result unless a documented policy explicitly permits a descriptive observation or excludes a metric.

## Implementation map

| Stage | Implemented check | Source |
|---|---|---|
| Declared configuration | Read deployment replicas and HPA minimum; wait for declared readiness before load | [Cold-start helpers](../scripts/lib/cold_start.sh) |
| Observed replica validation | Verify warm-up stays at each arm's floor; require fixed in-window peak ready replicas to reach its declaration | [Arm helpers](../scripts/lib/phase5_arm.sh), [collector](../analysis/collect_metrics.py) |
| Metric-arm isolation | Query experiment labels; reject opposite-arm request increases in the measurement window | [Collector](../analysis/collect_metrics.py), [Prometheus relabeling](../k8s/prometheus/configmap.yaml) |
| At least 95% data coverage | Enforce required-column coverage over serving rows | [Metric contract](../analysis/metrics_contract.py) |
| Required artifacts present | Validate Locust stats and measured-shape completion; verify bundle arm inventory, completion extracts, hashes and summaries | [Locust validator](../scripts/lib/locust_validate_arm.py), [publication verifier](../artifacts/v1.1/verify.py) |
| Explicit exclusions applied | Exclude contaminated tuned flash rep-1 ready-pod time; retain its latency; omit incomplete ramp rep-3 | [Executable exclusions](../artifacts/v1.1/exclusions.csv) |
| Publishable result | Recompute and compare summaries with the retained evidence and policy | [Verifier](../artifacts/v1.1/verify.py), [rejection tests](../tests/test_publication.py) |

## Exact scope of the gates

`REPLICA_BELOW_DECLARED` blocks fixed-arm collection if peak in-window `ready_replicas` never reaches the declared count. A temporary dip logs `REPLICA_DIP_OBSERVED` and continues. HPA scale-out is assessed with `spec_replicas`, the desired count, separately from ready replicas.

`HPA_NEVER_SCALED` normally aborts an HPA arm that never exceeds its minimum. The runner permits a `warn` policy for constant and periodic workloads and records explicit overrides for other scoped runs. Non-scaling observations therefore need their policy context. The retained ramp scope includes such observations. Equal declared floors are specified in current manifests; the per-arm guards do not constitute a separate cross-arm equality assertion.

Coverage uses rows with `ready_replicas > 0`. Rate-derived columns exclude the first two serving rows to account for burst-onset lookback. Zero-ready rows are `UNAVAILABLE`; their metric cells are `TARGET_UNAVAILABLE`. Serving rows are `DEGRADED` or `AVAILABLE` and contain measured values or `MISSING`. Availability reports each state out of all rows. The 95% threshold is unchanged.

Reachability and wall-clock guards bound Locust execution. Stats CSVs plus shape completion establish measured-arm success, independently of exit status. Warm-up has no shape and instead requires nonzero Aggregated requests. Recovery options that skip coverage are explicitly logged; they cannot substantiate a coverage-certified result.

## What can be checked offline

The v1.1 bundle verifies its retained stats, replica series, completion extracts, exclusions, hashes and derived summaries. It does **not** include Prometheus CSVs. It cannot recheck historical arm isolation or coverage, or establish that every collector gate passed during every original arm. The diagram is an implementation map, not a certification of omitted evidence.

The contaminated sampler series is preserved in full. Publication excludes its ready-pod integral rather than silently trimming or filling it. Its anchored peak and its separate Locust latency measurement remain usable under the explicit policy. The historical process responsible for the contamination is not independently established.

For offline commands and the separate cloud collection path, see [REPRODUCE.md](../REPRODUCE.md).
