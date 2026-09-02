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
New runs write under `results/runs/<run_id>/rep-N/` with `data_source=MEASURED`.
