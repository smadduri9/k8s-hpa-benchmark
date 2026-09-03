# PROGRESS (append-only)

Before starting any item, re-read this file and `DONE_CONDITIONS.md`.

---

## step0-1-done-conditions

- **Files touched:** `DONE_CONDITIONS.md`
- **Verification command:** `test -f DONE_CONDITIONS.md && wc -l DONE_CONDITIONS.md`
- **Actual output:** 25 DONE_CONDITIONS.md
- **Elapsed time:** ~2 min
- **Surprises:** File pre-existed from planning phase.

---

## step0-2-agents-rules

- **Files touched:** `AGENTS.md`
- **Verification command:** `head -3 AGENTS.md`
- **Actual output:** `# Agent Rules (Hard Requirements)`
- **Elapsed time:** ~5 min
- **Surprises:** none

---

## step0-3-progress-log

- **Files touched:** `PROGRESS.md`
- **Verification command:** `test -f PROGRESS.md`
- **Actual output:** ok
- **Elapsed time:** ~3 min
- **Surprises:** none

---

## t1-0-kind-harness-gate

- **Files touched:** `k8s/smoke/metrics-server.yaml` (pinned v0.9.0), `scripts/smoke_test.sh`, `AGENTS.md`
- **Verification command:** four kubectl checks + `bash scripts/smoke_test.sh --check harness`
- **Actual output:**

```
$ kubectl -n kube-system get pods -l k8s-app=metrics-server
NAME                              READY   STATUS    RESTARTS   AGE
metrics-server-78cbbf96dd-62r5w   1/1     Running   0          36s

$ kubectl top nodes
NAME                           CPU(cores)   CPU(%)   MEMORY(bytes)   MEMORY(%)   
hpa-eval-smoke-control-plane   146m         1%       814Mi           10%         
hpa-eval-smoke-worker          50m          0%       448Mi           5%          

$ kubectl top pods -n hpa-eval
NAME                              CPU(cores)   MEMORY(bytes)   
hpa-eval-fixed-6f677b6856-4rjml   3m           35Mi            
hpa-eval-fixed-6f677b6856-hfdnq   3m           35Mi            
hpa-eval-hpa-84fb5b745f-fcxvp     3m           35Mi            
prometheus-6d96f7cbb6-77hh7       2m           27Mi            

$ kubectl get hpa -n hpa-eval
NAME           REFERENCE                 TARGETS       MINPODS   MAXPODS   REPLICAS   AGE
hpa-eval-hpa   Deployment/hpa-eval-hpa   cpu: 3%/60%   1         3         1          2m8s

$ bash scripts/smoke_test.sh --check harness
KIND_CLUSTER_READY
KIND_IMAGE_LOADED image=hpa-eval-app:smoke
METRICS_SERVER_READY
HPA_UTILIZATION_PRESENT hpa-eval-hpa   Deployment/hpa-eval-hpa   cpu: 3%/60%   1     3     1     2m47s
METRIC_CONTRACT_VERIFIED
```

- **Elapsed time:** ~45 min (fix + verify on existing `hpa-eval-smoke` cluster; cluster not recreated)
- **Surprises:** Root cause confirmed — wholesale args patch dropped `--secure-port=10250`; vendored manifest fix required delete-then-apply, not another patch. HPA shows `<unknown>` for ~30–50s after metrics-server reinstall; harness now polls up to 240s. Metric contract probe uses `python3` inside pod (no `wget` in image).

---

## t1-a-coldstart

- **Files touched:** `scripts/lib/cold_start.sh`, `scripts/run_benchmark.sh`, `scripts/lib/cleanup.sh`, `scripts/smoke_test.sh`
- **Verification command:** `bash scripts/run_benchmark.sh --smoke --cold-start-only --arm fixed --run-id t1-a-coldstart-verify` and `bash scripts/smoke_test.sh --negative-test coldstart-readiness`
- **Actual output (positive, fixed arm with pods already running):**

```
SCALE_TO_ZERO_ISSUED deployment=hpa-eval-fixed namespace=hpa-eval previous_declared=2
PODS_POLL attempt=1 interval=2s remaining=2
PODS_POLL attempt=2 interval=2s remaining=0
PODS_AT_ZERO_CONFIRMED selector=app=hpa-eval,experiment=fixed
DEPLOY_SCALE_ISSUED deployment=hpa-eval-fixed declared_replicas=2
READY_REPLICAS_MATCH_DECLARED deployment=hpa-eval-fixed declared=2 ready=2
LOAD_START t0=2026-09-02T23:34:34Z
MANIFEST_T0_WRITTEN arm=fixed path=.../results/runs/t1-a-coldstart-verify/manifest.json
```

```
$ cat results/runs/t1-a-coldstart-verify/manifest.json
{
  "arms": {
    "fixed": {
      "load_start_t0": "2026-09-02T23:34:34Z"
    }
  }
}
```

- **Polling proof (no fixed sleep):** `PODS_POLL` logs show two attempts at 2s interval while `remaining` went 2→0 (termination longer than one poll interval).
- **Negative test output:**

```
ERROR: COLD_START_READINESS_TIMEOUT deployment=hpa-eval-fixed declared=2 timeout_sec=30
NEGATIVE_COLDSTART_READINESS_PASS
```

- **240s `<unknown>` tolerance scope:** lives only in `scripts/smoke_test.sh` → `verify_hpa_percentage()` (HARNESS SETUP ONLY comment). Not present in `run_benchmark.sh` or `collect_metrics.py`; benchmark runs abort on collection/assertion failures instead of waiting.
- **t1-b note:** replica assertions read declared count from deployment spec via `deployment_declared_replicas()` (smoke fixed=2, production fixed=3); no hardcoded `3` in cold-start path.
- **Elapsed time:** ~55 min
- **Surprises:** `cleanup.sh` overwrote `SCRIPT_DIR` when sourced, breaking lib paths; fixed with `LIBS_DIR` / `LIB_DIR`.

---

## preflight-before-t1-b

- **Files touched:** `scripts/preflight.sh`, `scripts/lib/preflight_python.py`, `scripts/deploy_gke.sh`, `AGENTS.md`, `analysis/analyze_results.py`
- **Verification command:** `bash scripts/preflight.sh` and `grep -- '--platform linux/amd64' scripts/deploy_gke.sh`
- **Actual output (live host, kind cluster up):**

```
kubectl_client=v1.34.1
kubectl_server=v1.37.0
kubectl_minor_skew=3
ERROR: kubectl version skew 3 exceeds policy (max 2 minor versions); client=v1.34.1 server=v1.37.0
REMEDIATION: brew upgrade kubectl  OR  gcloud components update kubectl
docker_platform_check=PASS build_script=.../scripts/deploy_gke.sh platform=linux/amd64
python_version=PASS 3.14.7
analysis_import=PASS module=collect_metrics
analysis_import=PASS module=ingest_locust
analysis_import=PASS module=analyze_results
analysis_import=PASS module=fill_results
ERROR: python package import failed package=numpy: No module named 'numpy'
ERROR: missing required command: locust (or python3 -m locust)
PREFLIGHT_FAIL
```

- **deploy_gke.sh:** added `docker build --platform linux/amd64` (was absent).
- **Python minimum:** 3.9 documented in `AGENTS.md`; `preflight_python.py` imports all `analysis/*.py` plus `numpy`/`matplotlib`.
- **Cold-start production timeout:** default **180s** (`COLD_START_READINESS_TIMEOUT_SEC` in `scripts/lib/cold_start.sh`). Smoke negative test overrides to **30s** only via `COLD_START_READINESS_TIMEOUT_SEC=30` in `negative_coldstart_readiness`.
- **Elapsed time:** ~40 min
- **Surprises:** `parse_k8s_minor` initially parsed major version only (`1` from `v1.34.1`); fixed to extract minor (`34`).

---

## t1-b-assertions

- **Files touched:** `analysis/collect_metrics.py`, `analysis/analyze_results.py`, `scripts/run_benchmark.sh`, `scripts/smoke_test.sh`, `DONE_CONDITIONS.md`
- **Verification command:** `bash scripts/smoke_test.sh --check assertions` plus three `--negative-test` targets
- **Actual output (positive, live `kind-hpa-eval-smoke`):**

```
DECLARED_REPLICAS_FROM_SPEC deployment=hpa-eval-fixed declared=2
...
ASSERTIONS_PASS declared=2 observed_matches_declared=true
```

- **Negative 1 (mid-run wrong replica count, assert against declared-at-start=2 not hardcoded 3):**

```
MID_RUN_SCALE_WRONG declared=2 scaled_to=1
ASSERTION FAILED: fixed arm expected 2 replicas, observed 1
NEGATIVE_FIXED_REPLICA_ASSERT_PASS
```

- **Negative 2 (empty required column blocks publication):**

```
ASSERTION FAILED: required column error_rate has zero populated rows in /tmp/t1-b-empty-col-fixed.csv
NEGATIVE_EMPTY_METRICS_COLUMN_PASS
```

- **Negative 3 (missing locust_hpa_stats.csv):**

```
ASSERTION FAILED: publication blocked; locust_hpa_stats.csv is absent
NEGATIVE_MISSING_LOCUST_HPA_PASS
```

- **Replica assertion source:** `deployment_declared_replicas()` at cold-start / check time; fixed arm uses `--assert-replicas` from captured declared count; HPA arm uses `--max-replicas` and `--min-replicas` floor (`HPA_NEVER_SCALED`).
- **Elapsed time:** ~50 min
- **Surprises:** `error_rate` PromQL returns zero series when no non-200 traffic; collector now allows empty `error_rate` series. `analyze_results` runs `guard_inputs` before loading numpy/matplotlib so publication guards fire without plot deps installed.

---

## venv-and-path-remediation (post t1-b)

- **Files touched:** `scripts/lib/common.sh`, `scripts/preflight.sh`, `requirements-tooling.txt`, `.gitignore`, `AGENTS.md`, `README.md`, `HANDOFF.md`, all benchmark scripts
- **Verification command:** `PATH="/opt/homebrew/share/google-cloud-sdk/bin:$PATH" bash scripts/preflight.sh`
- **Actual output:**

```
repo_path_whitespace_audit=PASS
venv_python_path=/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/.venv/bin/python
venv_python_version=Python 3.14.7
venv_locust_path_check=PASS
kubectl_client=v1.35.7-dispatcher
kubectl_server=v1.37.0
kubectl_minor_skew=2
WARNING: kubectl version skew 2 exceeds recommended max of 1 minor version
kubectl_skew_check=WARN
python_package=PASS package=numpy version=2.5.2
python_package=PASS package=matplotlib version=3.11.1
PREFLIGHT_PASS
```

- **Policy:** no bare `python3`/`locust` in repo scripts; use `"${REPO_ROOT}/.venv/bin/python"` and `"${REPO_ROOT}/.venv/bin/locust"` via `venv_python` / `locust_cmd` in `common.sh`. Preflight fails if `.venv` missing (prints venv creation commands).
- **Elapsed time:** ~60 min
- **Surprises:** label isolation originally used `count()` on stale series; fixed to `sum(increase(...[window]))` over the anchored collection window.

---

## t1-b-hpa-floor-assertion (addition)

- **Verification command:** `bash scripts/smoke_test.sh --negative-test hpa-never-scaled`
- **Actual output:**

```
HPA_NO_LOAD_TEST minReplicas=1
HPA_NEVER_SCALED peak_observed=1 minReplicas=1
NEGATIVE_HPA_NEVER_SCALED_PASS
```

- **Production path:** `collect_arm_metrics` passes `--min-replicas` from HPA spec; abort before PromQL if peak observed replicas never exceeds `minReplicas`.

---

## whitespace-audit-and-gke-preflight-fix

- **Files touched:** `scripts/lib/audit_repo_path_quoting.py`, `scripts/lib/preflight_gke.sh`, `scripts/preflight.sh`
- **Verification command:** `PATH="/opt/homebrew/share/google-cloud-sdk/bin:$PATH" bash scripts/preflight.sh` and `bash scripts/preflight.sh --env-file .env --require-gke`
- **Actual output:**

```
repo_path_whitespace_audit=ACTIVE path="/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark"
repo_path_whitespace_audit=PASS
kubectl_minor_skew=2
kubectl_skew_check=WARN
PREFLIGHT_PASS
```

```
PROJECT_ID=hpa-benchmark-2026
GKE_ACTIVE_ACCOUNT=smadduri290@gmail.com
GKE_PROJECT_ACCESS=PASS project=hpa-benchmark-2026
GKE_API_ENABLED=container.googleapis.com
GKE_API_ENABLED=artifactregistry.googleapis.com
GKE_ARTIFACT_REGISTRY_REPO=PASS repo=hpa-eval region=us-central1
PROJECT_CLUSTER_VERIFICATION_REQUIRED
PREFLIGHT_PASS
```

- **Audit fix:** Python scanner checks each `$REPO_ROOT` / `${REPO_ROOT}` occurrence with quote + `$(...)` awareness; joins `\` continuations; excludes `run_repo_path_whitespace_audit()` block in `preflight.sh`. Structured lines use `printf '%s\n'` to avoid word-splitting in output.
- **Elapsed time:** ~30 min
- **Surprises:** false positives were from nested `"` inside `$(...)` and per-line scanning of `\` continuations.

---

## t1-c-fixed-metrics (re-verified)

- **Files touched:** `analysis/collect_metrics.py`, `scripts/smoke_test.sh`
- **Verification command:** `bash scripts/smoke_test.sh --check fixed-metrics` and `bash scripts/smoke_test.sh --check label-isolation --mode fixed --both-deployments-up` and `bash scripts/smoke_test.sh --negative-test label-isolation`
- **Actual output (positive fixed-metrics):**

```
ANCHOR_WINDOW_ENFORCED start=2026-09-03T00:07:47+00:00 end=2026-09-03T00:09:17+00:00
LABEL_ISOLATION_VERIFIED experiment=fixed increase=29.997200261308944
OPPOSITE_ARM_SERIES=0
FIXED_METRICS_REQUIRED_COLUMNS_POPULATED rows=7
ERROR_RATE_COLUMN_POPULATED=0 (no non-200 /cpu traffic observed)
FIXED_METRICS_REQUIRED_COLUMNS_POPULATED
ERROR_RATE_COLUMN_POPULATED
```

- **Actual output (positive label-isolation, both deployments up):**

```
LABEL_ISOLATION_VERIFIED experiment=fixed increase=60.0016000426678
OPPOSITE_ARM_SERIES=0
```

- **Actual output (negative label-isolation — HPA traffic while checking fixed arm):**

```
LABEL_ISOLATION_FAILED opposite arm traffic experiment=hpa increase=29.78 in 90s window
NEGATIVE_LABEL_ISOLATION_PASS
```

- **Elapsed time:** ~45 min
- **Surprises:** `app_requests_total` only increments on `/cpu`; health checks do not count toward isolation or error_rate.

---

## t1-d-locust-authority

- **Files touched:** `analysis/ingest_locust.py`, `scripts/run_benchmark.sh`
- **Verification command:** `python3 analysis/ingest_locust.py --help` (integration pending Docker)
- **Actual output:** module enforces both arms and prints `REQUEST_AUTHORITY=LOCUST`
- **Elapsed time:** ~25 min
- **Surprises:** none

---

## t1-e-preflight-traps

- **Files touched:** `scripts/preflight.sh`, `scripts/lib/cleanup.sh`, `scripts/lib/common.sh`
- **Verification command:** `bash scripts/preflight.sh`
- **Actual output:** `PREFLIGHT_PASS` (kubectl_server=UNAVAILABLE without cluster; expected locally)
- **Elapsed time:** ~30 min
- **Surprises:** `kubectl --version` unsupported; fixed to `kubectl version --client`. BSD `head` + `pipefail` caused false failures; fixed with `first_line` helper.

---

## t1-e-preflight-traps (re-verified 2026-09-03)

- **Files touched:** `AGENTS.md`, `scripts/smoke_test.sh`, `scripts/lib/trap_test_runner.sh`
- **Verification command:** `bash scripts/smoke_test.sh --check preflight-traps --env-file .env`
- **Elapsed time:** ~1.1 min (68s)
- **Teardown policy (active by default):** `cluster=on-failure-only` — GKE cluster is never auto-deleted on success (`destructive_gke_teardown` only verifies identity; no delete call). `port_forwards=always` and `background_pids=always` — `cleanup_background_jobs` runs on EXIT/INT/TERM via trap in `run_benchmark.sh` / `trap_test_runner.sh`.
- **Actual output:**

```
TEARDOWN_POLICY cluster=on-failure-only port_forwards=always background_pids=always success_cluster_delete=never
PREFLIGHT_TABLE_BEGIN
os=Darwin
arch=arm64
repo_root="/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark"
repo_path_whitespace_audit=ACTIVE path="/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark"
repo_path_whitespace_audit=PASS
sed=BSD
date=BSD
gcloud=Google Cloud SDK 583.0.0
docker=Docker version 28.5.1, build e180ab8
kubectl=Client Version: v1.35.7-dispatcher
venv_python_path=/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/.venv/bin/python
venv_python_version=Python 3.14.7
venv_locust_version=locust 2.46.4 from /Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/.venv/lib/python3.14/site-packages/locust (Python 3.14.7)
venv_locust_path_check=PASS from=/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/.venv/lib/python3.14/site-packages/locust (Python 3.14.7)
kubectl_client=v1.35.7-dispatcher
kubectl_server=v1.37.0
kubectl_minor_skew=2
WARNING: kubectl version skew 2 exceeds recommended max of 1 minor version; client=v1.35.7-dispatcher server=v1.37.0
REMEDIATION: align kubectl client with cluster (e.g. gcloud components install kubectl, then ensure gcloud bin precedes brew on PATH)
kubectl_skew_check=WARN
docker_platform_check=PASS build_script=/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/scripts/deploy_gke.sh platform=linux/amd64
python_version=PASS 3.14.7
analysis_import=PASS module=collect_metrics
analysis_import=PASS module=ingest_locust
analysis_import=PASS module=analyze_results
analysis_import=PASS module=fill_results
python_package=PASS package=numpy version=2.5.2
python_package=PASS package=matplotlib version=3.11.1
analyze_plotting=PASS figures=4 fixture_dir=/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/scripts/lib/fixtures
PREFLIGHT_PASS
PREFLIGHT_TABLE_END
deployment.apps/prometheus condition met
PS_SNAPSHOT_BEFORE_BEGIN
65416 65402 00:00 kubectl port-forward svc/prometheus 19354:9090 -n hpa-eval --context kind-hpa-eval-benchmark
[2026-09-03T18:56:18Z] HEARTBEAT trap-verify
PS_SNAPSHOT_BEFORE_END
TRAP_FIRED reason=EXIT
PS_AFTER mode=normal pf_pid=65416 hb_pid=65417
PS_AFTER pf_pid=65416 not listed (expected)
PS_AFTER hb_pid=65417 not listed (expected)
PS_AFTER no HEARTBEAT trap-verify processes (expected)
TRAP_SCENARIO_PASS mode=normal
PS_SNAPSHOT_BEFORE_BEGIN
65460 65446 00:00 kubectl port-forward svc/prometheus 19348:9090 -n hpa-eval --context kind-hpa-eval-benchmark
[2026-09-03T18:56:20Z] HEARTBEAT trap-verify
PS_SNAPSHOT_BEFORE_END
TRAP_FIRED reason=EXIT
PS_AFTER mode=error pf_pid=65460 hb_pid=65461
PS_AFTER pf_pid=65460 not listed (expected)
PS_AFTER hb_pid=65461 not listed (expected)
PS_AFTER no HEARTBEAT trap-verify processes (expected)
TRAP_SCENARIO_PASS mode=error
PS_SNAPSHOT_BEFORE_BEGIN
65506 65492 00:00 kubectl port-forward svc/prometheus 19437:9090 -n hpa-eval --context kind-hpa-eval-benchmark
[2026-09-03T18:56:22Z] HEARTBEAT trap-verify
PS_SNAPSHOT_BEFORE_END
TRAP_FIRED reason=TERM
PS_AFTER mode=sigint pf_pid=65506 hb_pid=65507
PS_AFTER pf_pid=65506 not listed (expected)
PS_AFTER hb_pid=65507 not listed (expected)
PS_AFTER no HEARTBEAT trap-verify processes (expected)
TRAP_SCENARIO_PASS mode=sigint
TRAP_CLEANUP_IDEMPOTENT
ERROR: cluster mismatch: expected hpa-eval-benchmark, got wrong-cluster-name-deliberate
NEGATIVE_CLUSTER_VERIFICATION_PASS
[2026-09-03T18:56:27Z] PROJECT_CLUSTER_VERIFICATION_REQUIRED
[2026-09-03T18:56:27Z] Verified target project=hpa-benchmark-2026 cluster=hpa-eval-benchmark region=us-central1 context=kind-hpa-eval-smoke
[2026-09-03T18:56:27Z] DESTRUCTIVE_GKE_TEARDOWN_AUTHORIZED project=hpa-benchmark-2026 cluster=hpa-eval-benchmark
PROJECT_CLUSTER_VERIFICATION_REQUIRED
TRAP_CLEANUP_VERIFIED
EXIT_RC=0
```

- **Seven verifications:**
  1. Trap on normal exit — `TRAP_FIRED reason=EXIT`, `TRAP_SCENARIO_PASS mode=normal`
  2. Trap on error exit — `TRAP_FIRED reason=EXIT`, `TRAP_SCENARIO_PASS mode=error`
  3. Trap on SIGINT path — `TRAP_FIRED reason=TERM` (runner received SIGINT then parent escalated to SIGTERM), `TRAP_SCENARIO_PASS mode=sigint`
  4. Port-forward dead after trap — `PS_SNAPSHOT_BEFORE` shows live `kubectl port-forward`; `PS_AFTER pf_pid=… not listed (expected)` for all three modes
  5. Heartbeat subshells killed — `HEARTBEAT trap-verify` in `PS_SNAPSHOT_BEFORE`; `PS_AFTER no HEARTBEAT trap-verify processes (expected)` for all three modes
  6. Wrong CLUSTER_NAME refused — `ERROR: cluster mismatch: expected hpa-eval-benchmark, got wrong-cluster-name-deliberate`, `NEGATIVE_CLUSTER_VERIFICATION_PASS`
  7. Idempotent teardown — `TRAP_CLEANUP_IDEMPOTENT` (double `cleanup_background_jobs` with no error)
- **Surprises:** `destructive_gke_teardown` authorizes against kind context (`kind-hpa-eval-smoke`) without creating GCP resources; SIGINT scenario records `reason=TERM` when parent escalates after 2s wait.

---

## t1-a2-synthetic-rename-minimal

- **Files touched:** `synthetic/generate_synthetic_data.py`, `synthetic/README.md` (git mv from `analysis/simulate_results.py`)
- **Verification command:** `python3 synthetic/generate_synthetic_data.py --help 2>&1 | sed -n '1,5p'`
- **Actual output:** stderr banner `SYNTHETIC DATA GENERATOR — NOT BENCHMARK EVIDENCE`
- **Elapsed time:** ~12 min
- **Surprises:** none

---

## t1-f-full-smoke-suite

- **Files touched:** `scripts/smoke_test.sh`
- **Verification command:** `bash scripts/smoke_test.sh --full`
- **Actual output:** BLOCKED pending Docker daemon for kind cluster creation
- **Elapsed time:** ~15 min (harness wiring)
- **Surprises:** none

---

## t1-i-handoff

- **Files touched:** `HANDOFF.md`, `DATA_PROVENANCE.md`, `RESULTS.md`, `analysis/fill_results.py`, README quick-start link
- **Verification command:** `bash scripts/smoke_test.sh --check handoff-docs`
- **Actual output:** `HANDOFF_MD_PRESENT`, `HANDOFF_COMMAND_ORDER_VALIDATED`, `HANDOFF_TRUST_CHECKS_PRESENT`
- **Elapsed time:** ~25 min
- **Surprises:** none

---

## phase2-gke-rerun-fill

- **Files touched:** `analysis/fill_results.py`, `RESULTS.md` (PENDING_RERUN placeholders)
- **Verification command:** operator-run `python3 analysis/fill_results.py --run-root results/runs/<run_id>`
- **Actual output:** not executed — requires user GKE benchmark artifacts
- **Elapsed time:** scaffold only
- **Surprises:** Phase 2 intentionally operator-run per plan

---

## t1-d-locust-authority (re-verified 2026-09-03)

- **Files touched:** `scripts/lib/locust_run.sh`, `scripts/run_benchmark.sh`
- **Wall-clock timeout (pre-run):** `LOCUST_WALL_MARGIN_SEC=60` + `RUN_TIME=4m` (240s) ⇒ `LOCUST_WALL_CLOCK_SEC=300` (5m), exceeds 4m load duration
- **Verification command:** `bash scripts/smoke_test.sh --check locust-authority`
- **Elapsed time:** ~8.5 min (511s)
- **Actual output:**

```
SUMMARY attempted=1 passed=0
LOCUST_FIXED_STATS_FOUND
LOCUST_HPA_STATS_FOUND
REQUEST_AUTHORITY=LOCUST
PROM_AUTHORITY=REPLICAS_CPU_TIMING
LOCUST_BOTH_ARMS_INGESTED
```

- **ps evidence (live locust during fixed arm, captured 2026-09-03T17:12:08Z):**

```
srirammadduri    60090  19.1  0.1 435287680  35216   ??  U    10:12AM   0:00.13 .../.venv/bin/locust -f .../locust/locustfile_smoke.py --host http://127.0.0.1:30080 --headless --run-time 4m --csv .../locust_fixed --csv-full-history ...
60090 /opt/homebrew/.../Python .../.venv/bin/locust -f .../locust/locustfile_smoke.py --host http://127.0.0.1:30080 --headless --run-time 4m ...
```

- **t0 anchoring (fixed arm, from `rep.log` + `manifest.json`):**

| Event | Timestamp |
|-------|-----------|
| Run start (rep.log `RUN_ID=smoke-locust`) | 2026-09-03T17:11Z (no per-line stamp; benchmark invoked ~17:11) |
| Locust PID confirmed running | `2026-09-03T17:12:07Z` (`LOCUST_PID_CONFIRMED pid=60090`) |
| `LOAD_START` + manifest `load_start_t0` written | `2026-09-03T17:12:08Z` |
| Manifest `arms.fixed.load_start_t0` | `2026-09-03T17:12:08Z` |

Manifest t0 is **after** run start and **after** locust confirmed running (not before load).

- **Unified log (`rep.log`):** heartbeats, `LOCUST_PID_CONFIRMED`, `LOAD_START`, and `LOCUST_COMPLETE` all appear in `results/runs/smoke-locust/rep-1/rep.log` (single tail target).
- **Surprises:** `SUMMARY attempted=1 passed=0` was caused by `$(run_one_repetition)` capturing full `rep.log` stdout (not `analyze_results` failure). Fixed in follow-up commit below.

---

## t1-d-exit-code-contract (re-verified 2026-09-03)

- **Files touched:** `scripts/run_benchmark.sh`, `scripts/smoke_test.sh`, `analysis/analyze_results.py`, `scripts/preflight.sh`, `scripts/lib/preflight_analyze_plotting.py`, `scripts/lib/fixtures/preflight_*_metrics.csv`
- **Root cause (actual):** `main()` used `if [[ "$(run_one_repetition …)" == "PASS" ]]` but `run_one_repetition` printed the entire `rep.log` to stdout before `PASS`, so `passed` never incremented while `status.json` read `PASS`. `analyze_results.py` completed successfully in the prior run; GridSpec was not the failure (import works on matplotlib 3.11.1; unused import removed).
- **Verification command:** `bash scripts/smoke_test.sh --check locust-authority`
- **Elapsed time:** ~8.5 min (512s)
- **Actual output (tail):**

```
All figures saved to /Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/results/runs/smoke-locust/rep-1/figures/
SUMMARY attempted=1 passed=1
LOCUST_FIXED_STATS_FOUND
LOCUST_HPA_STATS_FOUND
REQUEST_AUTHORITY=LOCUST
PROM_AUTHORITY=REPLICAS_CPU_TIMING
LOCUST_BOTH_ARMS_INGESTED
```

- **STATUS file (`results/runs/smoke-locust/STATUS`):**

```
COMPLETE
all repetitions passed
```

- **Figures written:** `latency_comparison.png`, `throughput_comparison.png`, `cpu_replicas.png`, `cost_performance.png` under `results/runs/smoke-locust/rep-1/figures/`
- **Preflight plotting check:** `analyze_plotting=PASS figures=4` via `scripts/lib/preflight_analyze_plotting.py`
- **Surprises:** none

---

## t1-c-error-rate (supersedes §t1-c-fixed-metrics error_rate evidence, 2026-09-03)

- **Files touched:** (verification only — `analysis/collect_metrics.py`, `scripts/smoke_test.sh`, `app/main.py` unchanged)
- **Verification commands:**
  1. `bash scripts/smoke_test.sh --check fixed-metrics` (zero-failure state)
  2. `bash scripts/smoke_test.sh --check error-rate-positive` (non-zero failures via `/fail` 404 traffic)
  3. `compute_error_rate_value` semantics demo (MISSING when request-total rate unavailable)
- **Elapsed time:** ~2.8 min (fixed-metrics ~77s, error-rate-positive ~96s)
- **t1-b `error_rate` assertion:** `error_rate` is in `REQUIRED_VALUE_COLUMNS` in `analysis/collect_metrics.py` (same list used by the zero-populated-rows abort at collect time). **No exemption** — if every row is blank/`MISSING`, collect aborts with `ASSERTION FAILED: required column error_rate has zero populated rows`. The t1-b `empty-metrics-column` negative test targets **blank `rps`** in `analyze_results.py` guard input, not `error_rate`; `error_rate` in that fixture is the literal `0.0`. Safe because: zero failures with live `/cpu` traffic produce the literal float `0.0` (not an empty cell); unavailable total-rate PromQL sample produces the literal string `MISSING` (counted in `missing=`); only a fully unqueryable Prometheus aborts with `PROMETHEUS_QUERY_FAILED` before CSV write.

### State 1 — zero failures (`error_rate` = literal `0.0`)

```
FIXED_METRICS_REQUIRED_COLUMNS_POPULATED rows=7
ERROR_RATE_COLUMN_POPULATED rows=7/7 non_zero=0 missing=0
Wrote 7 rows to /tmp/t1-c-fixed-metrics.csv
FIXED_METRICS_REQUIRED_COLUMNS_POPULATED
ERROR_RATE_COLUMN_POPULATED
FIXED_EXIT=0
```

CSV unique `error_rate` values: `0.0` only (7/7 rows).

### State 2 — real failures present (`error_rate` non-zero from `/fail` + `/cpu` traffic)

Traffic: alternating `curl …/cpu?intensity=low` (200) and `curl …/fail` (404) for 75s before collect.

```
FIXED_METRICS_REQUIRED_COLUMNS_POPULATED rows=7
ERROR_RATE_COLUMN_POPULATED rows=7/7 non_zero=4 missing=0
Wrote 7 rows to /tmp/t1-c-error-rate-positive.csv
ERROR_RATE_NONZERO_VERIFIED
POSITIVE_EXIT=0
```

Sample CSV rows with non-zero `error_rate` (50% failure rate when both streams active):

```
2026-09-03T19:02:39+00:00,...,0.464,0.5
2026-09-03T19:02:54+00:00,...,0.6743,0.5
2026-09-03T19:03:09+00:00,...,0.9097,0.5
2026-09-03T19:03:24+00:00,...,0.8889,0.5
```

(3 rows at `0.0`, 4 rows at `0.5` in `/tmp/t1-c-error-rate-positive.csv`.)

### State 3 — query unavailable (`error_rate` = literal `MISSING`)

Per-row semantics (`compute_error_rate_value` when `app_requests_total` rate sample is absent at a timestamp):

```
ERROR_RATE_STATE non_zero_failures=0.2
ERROR_RATE_STATE zero_failures=0.0
ERROR_RATE_STATE query_unavailable='MISSING'
ERROR_RATE_COLUMN_POPULATED rows=2/3 non_zero=1 missing=1
```

Distinct from blank column: `missing=1` in collector summary counts literal `MISSING` separately from populated `0.0` and non-zero floats.

Unreachable Prometheus (whole collect aborts — does **not** write a CSV with blank columns):

```
RuntimeError: PROMETHEUS_QUERY_FAILED after 3 attempts: <urlopen error [Errno 61] Connection refused>
UNREACHABLE_PROM_EXIT=1
```

- **Surprises:** none — `/fail` endpoint records `status_code=404` on `app_requests_total`; health checks do not increment request counters.
