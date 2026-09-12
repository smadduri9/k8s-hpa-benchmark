# HANDOFF — completed experiments and publication v1.1

The cluster is gone. Use the [public evidence package](artifacts/v1.1/README.md) to verify current findings without cloud resources.

```bash
".venv/bin/python" -B artifacts/v1.1/verify.py
".venv/bin/python" -B -m unittest discover -s tests -p test_publication.py
".venv/bin/python" -B scripts/generate_publication_text.py --check
".venv/bin/python" -B scripts/generate_public_figures.py
```

The publication verifier is the v1.1 authority. The historical general aggregator does not apply its sampler exclusion. `scripts/build_public_evidence.py` is a separate maintainer curation step that needs retained private inputs; external verification does not.

## Declared Phase 5 configuration

The repository manifests declare fixed **4** replicas, HPA **4–12** at **60%** CPU, **500m** CPU request and **1000m** CPU limit per application pod. Tuned behavior is in `k8s/hpa.yaml`; stock is in `k8s/hpa-stock.yaml`. The model-derived amplitude is **69**, not measured maximum capacity. Complete per-arm deployed configuration and hardware snapshots are not retained.

GKE defaults describe three `e2-standard-4` nodes and 50 GB boot disks. Quota and allocatable checks must use the target project and live resources before any future run. Old cost estimates and small-pod sizing calculations are superseded; they do not describe Phase 5. The [historical Tier 1 handoff](https://github.com/smadduri9/k8s-hpa-benchmark/blob/b8cf7cb2d22a53f82b1a4a9c61cbeabdf4bad0f7/HANDOFF.md) remains in Git history.

## Operator runbook for future experiments

The following commands create new local output or operate cloud resources. `results/` paths below are output destinations and private operator inputs, not published evidence links. Re-running experiments is separate from reproducing the publication calculations. No current cold-start distribution or billing result is claimed.

## Service type on GKE

`k8s/service.yaml` declares **`type: LoadBalancer`** for both `hpa-eval-fixed-svc` and `hpa-eval-hpa-svc`. On GKE each Service provisions a cloud load balancer.

**Keep LoadBalancer (do not switch to NodePort + port-forward).** `kubectl port-forward` is a single TCP tunnel through the API server. It can introduce an additional load-generator bottleneck; the benchmark uses externally reachable service endpoints instead. No load-balancer cost is estimated here.

`run_benchmark.sh` waits for both Services to receive an external IP and pass `GET /health` before cold-start begins (`LOADBALANCER_GATE_*` log markers). On timeout it exits with **`LOADBALANCER_NOT_READY`** so LB provisioning time is never folded into `t0`.

Prometheus remains `ClusterIP`; collect metrics via port-forward (as smoke tests do).

## `destructive_gke_teardown` limitation

`scripts/lib/cleanup.sh` → `destructive_gke_teardown` **verifies** `PROJECT_ID` and `CLUSTER_NAME` match `.env` before any destructive action. It logs `DESTRUCTIVE_GKE_TEARDOWN_AUTHORIZED` and **does not delete** the cluster or any GCP resource. **On-failure cluster teardown has never executed** in this repo — only the identity guard is tested (against kind context in smoke tests).

## Runner VM (same zone as GKE)

Provision with `bash scripts/provision_runner_vm.sh --env-file .env` (operator-only; creates billable GCP resources). VM name: **`hpa-bench-runner`**. Shape: `e2-standard-4` (4 vCPU), 50 GB boot disk, zone from `.env` `ZONE` (must match the GKE cluster zone). Image builds and `deploy_gke.sh` stay on the **Mac**; the runner has no Docker (`preflight.sh --skip-docker`).

**Runner VM must be STOPPED before cluster create.** Global `CPUS_ALL_REGIONS=12` cannot fit the cluster (12 vCPU) and the runner (4 vCPU) concurrently. Preflight fails with `RUNNER_VM_MUST_BE_STOPPED` if `hpa-bench-runner` is RUNNING. Stop it:

```bash
gcloud compute instances stop hpa-bench-runner --zone="${ZONE}" --project="${PROJECT_ID}"
```

Phase 5 load runs from the **Mac**, not the runner VM. Do not start the runner during the measurement matrix.

After the VM exists, bind `roles/container.developer` on the default compute service account (documented in the provision script; not applied automatically).

**tmux session `hpa-bench`:** start benchmarks inside tmux so SSH disconnect does not SIGTERM Locust or collection. Reattach with `tmux attach -t hpa-bench`.

**Results return:** from the Mac, `rsync` pull after `results/runs/<run_id>/STATUS` is written (or mid-run for partial reps). `results/` is gitignored.

```bash
rsync -avz USER@RUNNER_HOST:~/k8s-hpa-benchmark/results/runs/<run_id>/ results/runs/<run_id>/
```

On the VM after clone and `.venv`:
```bash
tmux new -s hpa-bench
bash scripts/run_benchmark.sh --env-file .env --repetitions 1
```

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

### 3) GKE abbreviated smoke while watching (~25–35 min)
```bash
bash scripts/deploy_gke.sh --env-file .env
bash scripts/run_benchmark.sh --env-file .env --smoke --repetitions 1
```

### 3b) P1 capacity probe (derive `SHAPE_MEAN_USERS`; ≤12 min; fixed arm only)

Run once after cluster deploy, **before** the Phase 5 matrix. Requires metrics-server (`kubectl top pods` must return data).

```bash
caffeinate -i bash scripts/run_capacity_probe.sh --env-file .env
```

Writes `results/capacity_probe/derivation.json`, `steps.csv`, and `probe.log`. CPU source: **metrics-server** (`CPU_SOURCE=metrics-server`, median millicores from `kubectl top pods -l app=hpa-eval,experiment=fixed` during load — sampled `PROBE_CPU_SAMPLE_LEAD_SEC` before step end, default 5s). Preflight uses the **same selector** and requires four fixed pods (`METRICS_SERVER_SELECTOR_EMPTY` if zero). Stop rules: `cpu_saturation` at 800m (80% of 1000m limit); `rps_per_user_drop` only after step ≥3, prior median CPU ≥100m, and >10% RPS-per-user decline (sub-10% is noise). RPS is full-window Locust `Requests/s` (includes ~1s spawn transient). Named errors: `CAPACITY_PROBE_TIMEOUT`, `METRICS_SERVER_UNAVAILABLE`, `CAPACITY_PROBE_NO_FEASIBLE_U`.

### 3c) Phase 5 matrix (tiered / dress rehearsal)

```bash
# Dress rehearsal: one wc98_flash rep, all three arms (~plan log wall_estimate before start)
caffeinate -i bash scripts/run_phase5_matrix.sh --env-file .env \
  --shapes wc98_flash --max-reps 1

# One tier (e.g. flash only, full n=6); compose with --resume after interruption
caffeinate -i bash scripts/run_phase5_matrix.sh --env-file .env \
  --shapes wc98_flash

# Full matrix (default: all five shapes, defined reps each)
caffeinate -i bash scripts/run_phase5_matrix.sh --env-file .env
```

`--shapes` is comma-separated; order follows the matrix definition. `--max-reps` caps reps per shape; capped runs record `matrix_repetitions_defined` vs `matrix_repetitions_requested` in each shape's `manifest.json` so aggregation cannot treat a partial run as complete. Unknown shape names fail with `PHASE5_MATRIX_UNKNOWN_SHAPE`.

### 4) Full GKE benchmark (walk-away)
```bash
nohup bash scripts/run_benchmark.sh --env-file .env --repetitions 1 > results/latest.nohup.log 2>&1 &
```

Pass `--repetitions 3` explicitly only when you want a multi-run statistical sample (default is `1`).

### 5) Post-run status checks
```bash
cat results/runs/<run_id>/STATUS
tail -f results/runs/<run_id>/rep-1/rep.log
".venv/bin/python" analysis/analyze_results.py --fixed results/runs/<run_id>/rep-1/fixed_metrics.csv --hpa results/runs/<run_id>/rep-1/hpa_metrics.csv --locust-hpa-stats results/runs/<run_id>/rep-1/locust_hpa_stats.csv
```

### 6) Post-run GCP orphan cleanup verification

Run after **every** GKE session and **after every cluster teardown**. GKE deletes load balancer forwarding rules when the cluster is deleted, but **persistent disks**, **static IPs**, and **firewall rules** are often retained. Deleting a GKE cluster does **not** delete dynamically provisioned PersistentVolumes or their backing disks. Leftover forwarding rules and `k8s-*` firewall rules block VPC deletion.

**Prometheus PVC (`prometheus-data`).** GKE deploy applies `k8s/prometheus/pvc.yaml`. Inspect unused disks and other resources after teardown; do not assume cluster deletion removed every resource. Resource retention depends on reclaim policy and teardown behavior. Use explicit project and zone values supplied by the operator.

```bash
gcloud container clusters list --project="${PROJECT_ID}"
gcloud compute forwarding-rules list --project="${PROJECT_ID}"
gcloud compute target-pools list --project="${PROJECT_ID}"
gcloud compute firewall-rules list --project="${PROJECT_ID}" --filter="name~'^k8s-'"
gcloud compute disks list --filter="-users:*" --project="${PROJECT_ID}"
gcloud compute addresses list --project="${PROJECT_ID}"
```

**Pass criteria:** clusters list empty (or only the cluster you expect); forwarding-rules, target-pools, `k8s-*` firewall rules, and unused disks/addresses empty or explicitly accounted for. Delete orphans before removing the VPC.

## Trust checks before publishing
1. `STATUS` is `COMPLETE` (or understand `PARTIAL` failure reasons in `rep-*/status.json`).
2. `kubectl get hpa -n hpa-eval` showed real `%` during smoke, not `<unknown>`.
3. `locust_hpa_stats.csv` exists for every repetition you plan to cite.
4. `LABEL_ISOLATION_VERIFIED` appears in smoke logs for both modes.
5. No `data_source=SYNTHETIC` in measured CSVs.
6. Every required metrics column shows `METRICS_COLUMN_COVERAGE … ratio≥0.95` in collector/analyzer output; publication aborts with `METRICS_COVERAGE_BELOW_THRESHOLD` otherwise.

## Notes
- Default `--repetitions` is `1`; pass `--repetitions 3` explicitly for statistical runs.
- Measured Locust uses `--headless --csv <base> --csv-full-history --exit-code-on-error 0` with explicit `--run-time`; never pass `--users`, `--spawn-rate`, or `--processes`.
- kind results are smoke-validation only and are not performance-comparable to GKE.
