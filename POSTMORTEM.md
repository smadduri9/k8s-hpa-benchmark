# Postmortem: a fixed baseline that was not fixed as declared

**Status:** original comparison withdrawn; measurement safeguards implemented.

**Author:** Sriram Madduri

**Scope:** historical benchmark failures. Current results are in [RESULTS.md](RESULTS.md).

## Summary

The original README claimed that HPA reduced the failure rate from **51.7% to 0.97%**. **This is the withdrawn original claim.** Verification later showed that the supposedly three-replica fixed baseline had actually been running with one replica. The comparison did not test the declared capacity policies and could not support the published conclusion.

The result was withdrawn. The project then rebuilt its measurement path around observed replica counts, isolated metrics, explicit coverage gates and required artifacts. The current publication has a separate evidence package and metric-specific exclusions.

## What failed

The original workflow trusted configuration intent without validating the replicas that actually served the workload. Its fixed-arm metrics also used unscoped queries and an unreliable replica proxy. HPA evidence had warm-start and cross-arm contamination problems, and the paired HPA Locust evidence was missing from the original sample package.

Those defects made a dramatic failure-rate gap look like a comparison of deployment strategies. It was not a valid estimate of an HPA benefit under the declared conditions. The [original sample files](docs/archive/superseded/sample_data-2026-03/) remain archived with a [provenance warning](DATA_PROVENANCE.md#historical-and-synthetic-material). Their incomplete replica fields cannot independently reconstruct the original deployment.

## A later, distinct fairness error

A subsequent September run declared fixed capacity at three replicas while HPA had a minimum of one. Its **12.07% versus 0.30% failure-rate comparison is also superseded**. It had unequal declared starting capacity, a different defect from the original fixed baseline running below its declaration.

The scale-out check alone did not detect this problem: an HPA going above its own minimum says nothing about whether that minimum matches the fixed arm. Historical calibrated reruns later raised the HPA floor to three. Current manifests declare four for both fixed capacity and the HPA minimum.

The [historical tables and figures](docs/archive/SUPERSEDED_RESULTS.md) preserve the later experiments. Their raw run inputs are not part of the v1.1 package, and they do not support its current findings.

## Detection and impact

Detection came from manual verification of measurement output and configuration, rather than an alert. The exact detection timestamp is `MISSING`. Earlier prose attributed both incidents to the unequal HPA floor and gave an imprecise detection lag; that account conflated separate failures.

The public README overstated what the evidence demonstrated. Readers could have interpreted the failure-rate difference as a fair comparison or reproduced the same confound from old manifests. No downstream adoption or operational impact is established by the retained record.

## Changes to the measurement system

| Failure mode | Implemented response | Boundary |
|---|---|---|
| Declared replicas differ from observations | Readiness checks before load; in-window fixed-arm ready-replica validation | The fixed collection check requires the observed peak to reach the declaration; mid-run dips are recorded separately |
| Warm-up changes the starting state | Check each arm remains at its declared floor after warm-up | Per-arm checks do not independently prove equal policy settings across arms |
| Metrics mix traffic from both arms | Experiment labels and opposite-arm traffic checks | Requires retained Prometheus evidence to verify a historical outcome |
| Missing or sparse data looks complete | Required-column coverage of at least 95% over serving rows | Rate columns exclude the first two serving rows; unavailable rows are classified separately |
| Locust exit status is mistaken for completion | Require stats CSVs and measured-shape completion | Warm-up has no shape and validates nonzero requests only |
| A replica series extends beyond its arm | Explicit publication exclusion for affected ready-pod time | Latency is retained; the contaminated raw series remains unchanged |
| Public tables drift from evidence | Offline verifier, calculation/rejection tests and generated-text checks | Hashes establish consistency within the package, not independent attestation |

See the [guard implementation map](docs/MEASUREMENT_GUARDS.md) for source links and policy exceptions. The original GKE cluster has been deleted; these safeguards were not rerun against that environment during publication cleanup.

## Historical milestones

| Record | Change |
|---|---|
| Initial publication (`951b89e`) | Original failure-rate claim published |
| Reproducibility remediation (`12b2f1c`) | Sample data quarantined and results replaced with a rerun scaffold |
| Later September comparison | Unequal declared floors remained a confound |
| Floor correction (`2439a8d`) | Historical HPA minimum raised to three |
| Current publication evidence (`1aa381b`) | Retained inputs, executable exclusions, summaries and standalone verifier published in the repository |

The [development archive](docs/archive/README.md) retains implementation notes and earlier acceptance records. Historical SLO and cost-model calculations are not current publication claims.

## Remaining lessons

Validate observed state before interpreting an outcome. A correct-looking manifest is insufficient evidence of the tested baseline.

Treat each safeguard as a specific assertion. Scale-out, equal starting capacity, metric isolation and data completeness are different properties.

Preserve failed evidence and narrow exclusions to the affected claim. A withdrawn result should remain understandable, while current results should lead readers to independently checkable inputs.
