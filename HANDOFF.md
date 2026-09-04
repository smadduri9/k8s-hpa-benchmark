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

## Expected GKE spend (zonal cluster)

Cluster shape from `scripts/deploy_gke.sh` (after zonal fix):

| Item | Value |
|------|--------|
| Topology | **Zonal** (`ZONE=us-central1-a`), not regional |
| Machine type | `e2-standard-2` |
| Node count | **3** in one zone (`--num-nodes=3`, autoscale 2–6) |
| Control-plane fee | **Waived** for the first zonal cluster per GCP project |

**Rough 3-hour unattended session estimate** (us-central1, on-demand, excludes egress and disk orphans):

| Component | Estimate |
|-----------|----------|
| 3× `e2-standard-2` compute (~$0.067/hr each) | ~$0.60 |
| 2× `LoadBalancer` Services (see below, ~$0.025/hr each) | ~$0.15 |
| **Total ballpark** | **~$0.75–$1.00** |

Autoscale above 3 nodes or leaving load balancers/disks after teardown increases cost. Regional topology would have been ~3× node cost plus a non-waived management fee — the deploy script now uses zonal explicitly to avoid that.

## Service type on GKE

`k8s/service.yaml` declares **`type: LoadBalancer`** for both `hpa-eval-fixed-svc` and `hpa-eval-hpa-svc`. On GKE each Service provisions a cloud load balancer — the most common cost orphan after teardown.

**Recommendation for cost-sensitive runs:** patch to `NodePort` (as the kind smoke kustomize overlay already does) and reach apps via `kubectl port-forward` or a single ingress you control. `run_benchmark.sh` accepts `--fixed-host` / `--hpa-host` flags for non-LoadBalancer endpoints.

Prometheus remains `ClusterIP`; collect metrics via port-forward (as smoke tests do).

## `destructive_gke_teardown` limitation

`scripts/lib/cleanup.sh` → `destructive_gke_teardown` **verifies** `PROJECT_ID` and `CLUSTER_NAME` match `.env` before any destructive action. It logs `DESTRUCTIVE_GKE_TEARDOWN_AUTHORIZED` and **does not delete** the cluster or any GCP resource. **On-failure cluster teardown has never executed** in this repo — only the identity guard is tested (against kind context in smoke tests).

## Exact commands (in order)

### 1) Preflight (~1 min)
```bash
cp .env.example .env   # fill PROJECT_ID, REGION, ZONE, CLUSTER_NAME, ARTIFACT_REGISTRY_REPO
python3 -m venv .venv
".venv/bin/python" -m pip install -r requirements-tooling.txt
bash scripts/preflight.sh --env-file .env --require-gke
```

### 2) Kind smoke gate (~15–25 min first run; ~15 min with fresh locust)
```bash
bash scripts/smoke_test.sh --check harness
bash scripts/smoke_test.sh --full --env-file .env
```

Use `--reuse-artifacts` only when intentionally skipping a fresh locust benchmark (prints `REUSED_ARTIFACTS run_id=…`).

### 3) GKE abbreviated smoke while watching (~10 min)
```bash
bash scripts/deploy_gke.sh --env-file .env
bash scripts/run_benchmark.sh --env-file .env --smoke --repetitions 1
```

### 4) Full GKE benchmark (walk-away)
```bash
nohup bash scripts/run_benchmark.sh --env-file .env --repetitions 3 > results/latest.nohup.log 2>&1 &
```

### 5) Post-run status checks
```bash
cat results/runs/<run_id>/STATUS
tail -f results/runs/<run_id>/rep-1/rep.log
".venv/bin/python" analysis/analyze_results.py --fixed results/runs/<run_id>/rep-1/fixed_metrics.csv --hpa results/runs/<run_id>/rep-1/hpa_metrics.csv --locust-hpa-stats results/runs/<run_id>/rep-1/locust_hpa_stats.csv
```

### 6) Post-run GCP orphan cleanup verification

Run after **every** GKE session. GKE deletes load balancer forwarding rules when the cluster is deleted, but **persistent disks** and **static IPs** are often retained. Leftover load balancers block VPC deletion.

Replace project id if yours differs:

```bash
gcloud container clusters list --project=hpa-benchmark-2026
gcloud compute forwarding-rules list --project=hpa-benchmark-2026
gcloud compute target-pools list --project=hpa-benchmark-2026
gcloud compute disks list --filter="-users:*" --project=hpa-benchmark-2026
gcloud compute addresses list --project=hpa-benchmark-2026
```

**Pass criteria:** clusters list empty (or only the cluster you expect); forwarding-rules, target-pools, and unused disks/addresses empty or explicitly accounted for. Delete orphans before removing the VPC.

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
