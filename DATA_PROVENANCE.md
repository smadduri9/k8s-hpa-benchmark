# Data Provenance

## Superseded artifacts (`SUPERSEDED`)
Moved from `sample_data/` to `superseded/sample_data-2026-03/` on reproducibility remediation.

| File | Status | Reason |
|---|---|---|
| `fixed_metrics.csv` | SUPERSEDED | Unscoped PromQL and replica proxy produced invalid fixed-arm evidence |
| `hpa_metrics.csv` | SUPERSEDED | Warm-start and cross-arm series contamination invalidated comparison |
| `locust_fixed_stats.csv` | SUPERSEDED | Retained for audit only; HPA Locust evidence was missing |
| `locust_fixed_stats_history.csv` | SUPERSEDED | Paired with superseded fixed-arm run |
| `locust_fixed_failures.csv` | SUPERSEDED | Paired with superseded fixed-arm run |
| `locust_fixed_exceptions.csv` | SUPERSEDED | Paired with superseded fixed-arm run |

## Synthetic artifacts
| Path | Status | Reason |
|---|---|---|
| `synthetic/generate_synthetic_data.py` | SYNTHETIC_TOOLING | Plot/UI development only; never benchmark evidence |

## Measured artifacts (current)
[artifacts/v1.1/](artifacts/v1.1/README.md) contains 33 completed Phase 5 arms,
five aggregate workload windows, capacity-probe evidence, executable exclusions
and generated summaries. [MANIFEST.json](artifacts/v1.1/MANIFEST.json) records
byte sizes, hashes, raw/derived status, transformations and units. The bundled
verifier recomputes the current findings offline.

Original collection output under `results/` and source datasets under `traces/`
are gitignored local inputs, not public evidence links. The package preserves
measured stats and replica CSVs byte-for-byte, including the contaminated flash
tuned rep-1 sampler. Its ready-pod time is excluded; its latency remains.
Incomplete ramp rep-3 is not included.

Workload windows contain aggregate counts, not individual source records.
[Source notices](artifacts/v1.1/SOURCE_NOTICES.md) specify redistribution terms.
Repository MIT licensing does not relicense third-party data; RetailRocket
derivatives retain CC BY-NC-SA 4.0.
