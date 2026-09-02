# HANDOFF — Reproducibility Remediation Tier 1

## What changed
- Added strict control files: `DONE_CONDITIONS.md`, `AGENTS.md`, `PROGRESS.md`.
- Added kind smoke harness (`scripts/smoke_test.sh`, `k8s/smoke/`, `locust/locustfile_smoke.py`).
- Added cold-start runner (`scripts/run_benchmark.sh`) with anchored metric collection.
- Rewrote `analysis/collect_metrics.py` for experiment-label isolation and kubectl replica sampling.
- Added `analysis/ingest_locust.py` as authoritative request/failure source.
- Superseded prior committed artifacts under `superseded/sample_data-2026-03/`.

## Verified on kind vs not verified
| Capability | kind smoke | GKE production |
|---|---|---|
| Cold-start scale-to-zero | yes | not yet |
| HPA utilization % | yes (reduced topology) | not yet |
| Label isolation | yes | not yet |
| 18-minute load shape | no (4-minute smoke only) | not yet |
| Cost/SLO modules | deferred Tier 2/3 | not yet |

## Exact commands (in order)

### 1) Preflight (~1 min)
```bash
cp .env.example .env   # fill PROJECT_ID, REGION, CLUSTER_NAME, ARTIFACT_REGISTRY_REPO
bash scripts/preflight.sh --env-file .env --require-gke
```

### 2) Kind smoke gate (~15-25 min first run)
```bash
bash scripts/smoke_test.sh --check harness
bash scripts/smoke_test.sh --full
```

### 3) GKE abbreviated smoke while watching (~10 min)
```bash
bash scripts/deploy_gke.sh   # existing deploy script with explicit env
bash scripts/run_benchmark.sh --env-file .env --smoke --repetitions 1
```

### 4) Full GKE benchmark (walk-away)
```bash
nohup bash scripts/run_benchmark.sh --env-file .env --repetitions 3 > results/latest.nohup.log 2>&1 &
```

### 5) Post-run status checks
```bash
cat results/runs/<run_id>/STATUS
tail -f results/runs/<run_id>/rep-1/run.log
python3 analysis/analyze_results.py --fixed results/runs/<run_id>/rep-1/fixed_metrics.csv --hpa results/runs/<run_id>/rep-1/hpa_metrics.csv --locust-hpa-stats results/runs/<run_id>/rep-1/locust_hpa_stats.csv
```

## Trust checks before publishing
1. `STATUS` is `COMPLETE` (or understand `PARTIAL` failure reasons in `rep-*/status.json`).
2. `kubectl get hpa -n hpa-eval` showed real `%` during smoke, not `<unknown>`.
3. `locust_hpa_stats.csv` exists for every repetition you plan to cite.
4. `LABEL_ISOLATION_VERIFIED` appears in smoke logs for both modes.
5. No `data_source=SYNTHETIC` in measured CSVs.

## Notes
- Default `--repetitions` is `1`; pass `--repetitions 3` explicitly for statistical runs.
- Locust uses `--headless --csv <base> --csv-full-history` only; never pass `--users`, `--spawn-rate`, or `--processes`.
- kind results are smoke-validation only and are not performance-comparable to GKE.
