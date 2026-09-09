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
| 18-minute load shape | no (10-minute smoke only; production 18m not yet on GKE) | not yet |
| Cost/SLO modules | deferred Tier 2/3 | not yet |

## Expected GKE spend (zonal cluster, fixed node pool)

Cluster shape from `scripts/deploy_gke.sh` (no cluster autoscaler — fixed node count only):

| Item | Value |
|------|--------|
| Topology | **Zonal** (`ZONE=us-central1-a`), not regional |
| Machine type | `e2-standard-4` (4 vCPU, 16 GB RAM per node) — Phase 5 default in `scripts/lib/gke_shape.sh` |
| Node count | **3** fixed (`GKE_NUM_NODES=3`; no `--enable-autoscaling`) |
| Boot disk | **50 GB** balanced PD per node (`NODE_DISK_SIZE_GB=50`; 3×50 = **150 GB** cluster SSD) |
| Global CPU quota | **`CPUS_ALL_REGIONS=12`** (project-wide). Cluster needs **12 vCPU** (3×4). No headroom — see quota section below. |
| Control-plane fee | **Waived** for the first zonal cluster per GCP project |

### App resources (`k8s/deployment-hpa.yaml`)

| | CPU | Memory |
|---|-----|--------|
| **request** | `100m` | `128Mi` |
| **limit** | `200m` | `256Mi` |

(`deployment-fixed.yaml` uses the same requests/limits; `replicas: 3` static.)

HPA `maxReplicas: 10` (`k8s/hpa.yaml`).

### Node-count arithmetic

GKE allocatable per `e2-standard-2` node (kube+system reserve): **~1930m CPU**, **~6172Mi** memory. Daemonsets per node: **~250m CPU**, **~400Mi** memory → **~1680m CPU** and **~5772Mi** schedulable per node.

Peak concurrent pod **requests** (worst case: HPA at maxReplicas while fixed arm still running):

| Workload | CPU request | Memory request |
|----------|-------------|----------------|
| HPA app × 10 | 1000m | 1280Mi |
| Fixed app × 3 | 300m | 384Mi |
| Prometheus × 1 | 100m | 256Mi |
| **Total** | **1400m** | **1920Mi** |

Solve for fixed nodes `N` (allocatable 1930m/node, daemonset overhead 250m/node):

`N × 1930m ≥ 1400m + N × 250m` → `N ≥ 1400 / (1930 − 250) ≈ 0.83` → **1 node suffices for requests**.

Limits at peak (`10×200m + 3×200m + 500m` Prometheus = **3100m**) exceed a single node's allocatable CPU; spreading across nodes reduces kubelet contention during HPA bursts.

**Chosen `N = 3`:** schedulable CPU `3 × 1680m = 5040m` vs demand `1400m + 3×250m = 2150m` (**~57% headroom** on requests). Memory headroom is ample (`1920Mi` vs `3 × 5772Mi`).

Cluster autoscaler was removed so node provisioning latency is not folded into HPA scaling measurements and both arms see the same fixed node baseline.

**Rough 3-hour unattended session estimate** (us-central1, on-demand, excludes egress):

| Component | Estimate |
|-----------|----------|
| 3× `e2-standard-2` compute (~$0.067/hr each) | ~$0.60 |
| 3× 50 GB balanced PD boot disks (~$0.10/GB-mo prorated; negligible for ≤3 hr) | ~$0.02 |
| 2× `LoadBalancer` Services (see below, ~$0.025/hr each) | ~$0.15 |
| **Total ballpark** | **~$0.75–$1.00** |

Default GKE boot disks are 100 GB balanced PD (300 GB total for 3 nodes), which exceeds this project's regional **`SSD_TOTAL_GB`** quota. `deploy_gke.sh` sets `--disk-size=50` explicitly.

### Quota preflight (`bash scripts/preflight.sh --env-file .env --require-gke`)

Preflight queries live quotas before cluster create. Expected Phase 5 rows:

| Metric | Scope | Required | Typical limit |
|--------|-------|----------|---------------|
| `CPUS` | Regional (`us-central1`) | 12 (3×4) | 32 |
| `SSD_TOTAL_GB` | Regional | 150 (3×50) | 250 |
| `INSTANCES` | Regional | 3 | 8 |
| **`CPUS_ALL_REGIONS`** | **Global (project)** | **12** | **12** |

**`CPUS_ALL_REGIONS` is the binding constraint.** Google declined a quota increase on usage-history grounds (billed spend $0 despite credits). Preflight fails with `QUOTA_INSUFFICIENT_CPUS_ALL_REGIONS` if cluster CPUs plus any **running** VM exceed 12.

When required equals the limit (`GKE_QUOTA_CPUS_ALL_REGIONS_NO_HEADROOM`), preflight still **PASS**es but emits a **WARN**: any transient usage or starting another instance will break cluster create.

Regional checks fail with `QUOTA_INSUFFICIENT_SSD`, `QUOTA_INSUFFICIENT_CPUS`, or `QUOTA_INSUFFICIENT_INSTANCES` when headroom is insufficient.

Autoscale above 3 nodes is disabled (fixed pool). Leaving load balancers/disks after teardown increases cost. Regional topology would have been ~3× node cost plus a non-waived management fee — the deploy script uses zonal explicitly to avoid that.

## Service type on GKE

`k8s/service.yaml` declares **`type: LoadBalancer`** for both `hpa-eval-fixed-svc` and `hpa-eval-hpa-svc`. On GKE each Service provisions a cloud load balancer.

**Keep LoadBalancer (do not switch to NodePort + port-forward).** `kubectl port-forward` is a single TCP tunnel through the API server. At 80 concurrent users for 18 minutes it becomes the bottleneck and the benchmark would measure the tunnel, not the HPA. The ~$0.15/session load-balancer cost is the correct trade for externally reachable endpoints.

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

Writes `results/capacity_probe/derivation.json`, `steps.csv`, and `probe.log`. CPU source: **metrics-server** (`CPU_SOURCE=metrics-server`, median millicores, stop at 800m = 80% of 1000m limit). Named errors: `CAPACITY_PROBE_TIMEOUT`, `METRICS_SERVER_UNAVAILABLE`, `CAPACITY_PROBE_NO_FEASIBLE_U` (stop and report — do not pick a compromise). The matrix reads `SHAPE_MEAN_USERS` from `derivation.json`; it will not run without that file.

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

**Prometheus PVC (`prometheus-data`).** GKE deploy applies `k8s/prometheus/pvc.yaml` (1 Gi `standard-rwo`). On the first teardown with this PVC present, the backing **pd-balanced** disk survived `gcloud container clusters delete` — confirmed orphan: `pvc-991b2700-b081-4bad-8f74-c27740792a92`. This is **expected behaviour** for dynamically provisioned PVs (Retain/reclaim policy leaves the disk in the project); it is not a GKE bug. Delete it manually:

```bash
gcloud compute disks delete pvc-991b2700-b081-4bad-8f74-c27740792a92 \
  --zone="${ZONE}" --project="${PROJECT_ID}"
```

(Use the name from `gcloud compute disks list --filter="-users:*"` — the UUID changes per cluster.)

Each orphaned Prometheus disk counts against regional **`SSD_TOTAL_GB`**. This project allocates 150 GB to the cluster (3×50 GB boot disks); with a 250 GB regional limit that leaves **~50 GB headroom**. A forgotten 1 Gi Prometheus disk is small but the pattern matters: repeated runs without cleanup erode quota and can block the next cluster create.

Replace project id if yours differs:

```bash
gcloud container clusters list --project=hpa-benchmark-2026
gcloud compute forwarding-rules list --project=hpa-benchmark-2026
gcloud compute target-pools list --project=hpa-benchmark-2026
gcloud compute firewall-rules list --project=hpa-benchmark-2026 \
  --filter="name~'^k8s-'"
gcloud compute disks list --filter="-users:*" --project=hpa-benchmark-2026
gcloud compute addresses list --project=hpa-benchmark-2026
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
- Locust uses `--headless --csv <base> --csv-full-history` only; never pass `--users`, `--spawn-rate`, or `--processes`.
- kind results are smoke-validation only and are not performance-comparable to GKE.
