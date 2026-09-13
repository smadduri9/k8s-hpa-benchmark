# Data provenance

## Current measured evidence

[artifacts/v1.1/](artifacts/v1.1/README.md) contains 33 completed arms, five aggregate workload windows, capacity-probe evidence, executable exclusions and generated summaries. [headline.json](artifacts/v1.1/summary/headline.json) is the canonical presentation input. The bundled verifier recomputes the current findings offline; see [REPRODUCE.md](REPRODUCE.md#verify-published-results).

[MANIFEST.json](artifacts/v1.1/MANIFEST.json) records byte sizes, hashes, raw/derived status, transformations and units. Measured stats and replica CSVs are preserved byte-for-byte, including the contaminated flash tuned rep-1 sampler. Its ready-pod time is excluded; its latency remains. Incomplete ramp rep-3 is omitted under the [exclusions policy](artifacts/v1.1/exclusions.csv).

Original collection output under `results/` and source datasets under `traces/` are gitignored operator inputs, not public evidence links. They are unnecessary for offline verification of the publication package. The minimal package does not include Prometheus CSVs or establish historical coverage, CPU utilization, SLO compliance, billing or a cold-start distribution.

## Workload lineage

Workload windows contain aggregate counts, not individual source records. The [bundled provenance](artifacts/v1.1/workloads/provenance/) describes the selected windows and normalized plateaus. Full candidate selection requires the original datasets. [Source notices](artifacts/v1.1/SOURCE_NOTICES.md) specify attribution and redistribution terms. Repository MIT licensing does not relicense third-party data; RetailRocket derivatives retain CC BY-NC-SA 4.0.

Active [candidate metadata](docs/shape_candidates/scoring_summary.json) uses portable repository-relative paths. The selection script emits repository-relative paths for outputs inside the checkout, or a filename relative to the scoring summary for an external output directory. No local machine identity is needed to interpret the selection.

## Historical and synthetic material

| Material | Status | Reason |
|---|---|---|
| [Original fixed metrics](docs/archive/superseded/sample_data-2026-03/fixed_metrics.csv) | SUPERSEDED | Unscoped PromQL and replica proxy invalidated fixed-arm evidence |
| [Original HPA metrics](docs/archive/superseded/sample_data-2026-03/hpa_metrics.csv) | SUPERSEDED | Warm-start and cross-arm contamination invalidated the comparison |
| [Original Locust files](docs/archive/superseded/sample_data-2026-03/) | SUPERSEDED | Retained fixed-arm stats, history, failures and exceptions; paired HPA Locust evidence was missing |
| [Later historical tables and figures](docs/archive/SUPERSEDED_RESULTS.md) | SUPERSEDED | Earlier synthetic or unequal-floor comparisons; later raw run evidence is not bundled |
| [Synthetic generator](synthetic/README.md) | SYNTHETIC_TOOLING | Plot development only; never benchmark evidence |

Historical material remains accessible under [docs/archive/](docs/archive/README.md). Archived development logs have sanitized local paths and account identifiers. Their measurements were not changed. Numeric evidence was not fabricated, filled or interpolated during this cleanup.
