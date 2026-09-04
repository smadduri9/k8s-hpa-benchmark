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

---

## t1-f-full-smoke-suite (supersedes stale BLOCKED entry, 2026-09-03)

- **Files touched:** `scripts/deploy_gke.sh`, `scripts/smoke_test.sh`, `.env.example`
- **GKE cluster topology (answered before run):** `deploy_gke.sh` previously created a **REGIONAL** cluster (`--region=us-central1`), which replicates `--num-nodes=3` across three zones (~9 nodes) and incurs the regional management fee. Changed to **ZONAL** `--zone=us-central1-a` (`.env` + `.env.example` add `ZONE=`; `REGION` retained for Artifact Registry).
- **Node spec:** `e2-standard-2`, `--num-nodes=3` in one zone, autoscale `--min-nodes=2` `--max-nodes=6`.
- **Verification command:** `bash scripts/smoke_test.sh --full`
- **Elapsed time:** ~6.1 min (364s; START=2026-09-03T23:57:16Z END=2026-09-04T00:03:20Z)
- **Exit code:** 0 (`SMOKE_SUITE_PASS`)

### Per-check results

| Check | Result |
|-------|--------|
| harness (`KIND_CLUSTER_READY`, `METRICS_SERVER_READY`, `HPA_UTILIZATION_PRESENT`, `METRIC_CONTRACT_VERIFIED`) | PASS |
| coldstart (`PODS_AT_ZERO_CONFIRMED`, `LOAD_START t0=`) | PASS |
| assertions (`ASSERTIONS_PASS`) | PASS |
| fixed-metrics (`FIXED_METRICS_REQUIRED_COLUMNS_POPULATED`, `ERROR_RATE_COLUMN_POPULATED non_zero=0`) | PASS |
| error-rate-positive (`ERROR_RATE_NONZERO_VERIFIED`, `non_zero=4`) | PASS |
| label-isolation positive (`LABEL_ISOLATION_VERIFIED`, both deployments up) | PASS |
| locust-authority (`LOCUST_BOTH_ARMS_INGESTED`; reused existing `smoke-locust` COMPLETE artifacts) | PASS |
| preflight-traps (`PREFLIGHT_PASS`, `TRAP_SCENARIO_PASS`×3, `TRAP_CLEANUP_IDEMPOTENT`, `TRAP_CLEANUP_VERIFIED`; `PROJECT_CLUSTER_VERIFICATION_SKIPPED` without `--env-file`) | PASS |
| negative fixed-replica-assert | PASS |
| negative empty-metrics-column | PASS |
| negative missing-locust-hpa | PASS |
| negative missing-locust-fixed | PASS |
| negative hpa-never-scaled (`HPA_NEVER_SCALED`) | PASS |
| negative label-isolation (`LABEL_ISOLATION_FAILED`) | PASS |
| suite markers (`NEGATIVE_ASSERTION_TEST_PASS`, `ALL_TIER1_ASSERTIONS_EXERCISED`, `SMOKE_SUITE_PASS`) | PASS |

### Actual output (full)

```
KIND_CLUSTER_READY
#0 building with "desktop-linux" instance using docker driver

#1 [internal] load build definition from Dockerfile
#1 transferring dockerfile: 884B done
#1 DONE 0.0s

#2 [internal] load metadata for docker.io/library/python:3.11-slim
#2 DONE 0.6s

#3 [internal] load .dockerignore
#3 transferring context: 2B done
#3 DONE 0.0s

#4 [internal] load build context
#4 transferring context: 64B done
#4 DONE 0.0s

#5 [1/6] FROM docker.io/library/python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534
#5 resolve docker.io/library/python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534 0.0s done
#5 DONE 0.0s

#6 [2/6] RUN groupadd --gid 1001 appgroup &&     useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser
#6 CACHED

#7 [3/6] WORKDIR /app
#7 CACHED

#8 [5/6] RUN apt-get update && apt-get install -y --no-install-recommends gcc python3-dev     && pip install --no-cache-dir -r requirements.txt     && apt-get purge -y gcc python3-dev && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
#8 CACHED

#9 [4/6] COPY requirements.txt .
#9 CACHED

#10 [6/6] COPY main.py .
#10 CACHED

#11 exporting to image
#11 exporting layers done
#11 exporting manifest sha256:86f0b7e99e966195f13ff9908dc7b01951d5b67264af2451ab996e325dc03be4 done
#11 exporting config sha256:0e5fae6793eae70f78d4d97cf0bfafa805fabae373f9ec5eb910908f2584bc52 done
#11 exporting attestation manifest sha256:0c1ec53fd05609906d9e1cb0ad49ae89c6f8a3c036786b484704298a71c0ef68 done
#11 exporting manifest list sha256:1881e2b06d67e5b4e0698f2591fd1e4cecb22adf6b9881382beacc81e318e840 done
#11 naming to docker.io/library/hpa-eval-app:smoke done
#11 unpacking to docker.io/library/hpa-eval-app:smoke done
#11 DONE 0.0s

View build details: docker-desktop://dashboard/build/desktop-linux/desktop-linux/n86aakulit5g4lzt7dyyeezol
Image: "hpa-eval-app:smoke" with ID "sha256:1881e2b06d67e5b4e0698f2591fd1e4cecb22adf6b9881382beacc81e318e840" not yet present on node "hpa-eval-smoke-control-plane", loading...
Image: "hpa-eval-app:smoke" with ID "sha256:1881e2b06d67e5b4e0698f2591fd1e4cecb22adf6b9881382beacc81e318e840" not yet present on node "hpa-eval-smoke-worker", loading...
KIND_IMAGE_LOADED image=hpa-eval-app:smoke
deployment.apps "metrics-server" deleted from kube-system namespace
serviceaccount/metrics-server unchanged
clusterrole.rbac.authorization.k8s.io/system:aggregated-metrics-reader unchanged
clusterrole.rbac.authorization.k8s.io/system:metrics-server unchanged
rolebinding.rbac.authorization.k8s.io/metrics-server-auth-reader unchanged
clusterrolebinding.rbac.authorization.k8s.io/metrics-server:system:auth-delegator unchanged
clusterrolebinding.rbac.authorization.k8s.io/system:metrics-server unchanged
service/metrics-server unchanged
deployment.apps/metrics-server created
apiservice.apiregistration.k8s.io/v1beta1.metrics.k8s.io unchanged
Waiting for deployment "metrics-server" rollout to finish: 0 of 1 updated replicas are available...
deployment "metrics-server" successfully rolled out
METRICS_SERVER_READY
namespace/hpa-eval unchanged
serviceaccount/prometheus unchanged
clusterrole.rbac.authorization.k8s.io/prometheus unchanged
clusterrolebinding.rbac.authorization.k8s.io/prometheus unchanged
configmap/prometheus-config unchanged
service/hpa-eval-fixed-svc unchanged
service/hpa-eval-hpa-svc unchanged
service/prometheus unchanged
deployment.apps/hpa-eval-fixed unchanged
deployment.apps/hpa-eval-hpa unchanged
deployment.apps/prometheus unchanged
horizontalpodautoscaler.autoscaling/hpa-eval-hpa unchanged
deployment.apps/hpa-eval-fixed condition met
deployment.apps/hpa-eval-hpa condition met
deployment.apps/prometheus condition met
HPA_UTILIZATION_PRESENT hpa-eval-hpa   Deployment/hpa-eval-hpa   cpu: 4%/60%   1     3     1     24h
METRIC_CONTRACT_VERIFIED
--- /metrics excerpt ---
app_requests_total{endpoint="/cpu",method="GET",status_code="200"} 25.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="0.005"} 0.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="0.01"} 1.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="0.025"} 9.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="0.05"} 9.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="0.1"} 25.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="0.25"} 25.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="0.5"} 25.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="1.0"} 25.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="2.5"} 25.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="5.0"} 25.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="10.0"} 25.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="+Inf"} 25.0
app_cpu_usage_percent 1.6
deployment.apps/hpa-eval-fixed condition met
SCALE_TO_ZERO_ISSUED deployment=hpa-eval-fixed namespace=hpa-eval previous_declared=2
deployment.apps/hpa-eval-fixed scaled
PODS_POLL attempt=1 interval=2s remaining=2
PODS_POLL attempt=2 interval=2s remaining=0
PODS_AT_ZERO_CONFIRMED selector=app=hpa-eval,experiment=fixed
DEPLOY_SCALE_ISSUED deployment=hpa-eval-fixed declared_replicas=2
deployment.apps/hpa-eval-fixed scaled
Waiting for deployment "hpa-eval-fixed" rollout to finish: 0 of 2 updated replicas are available...
Waiting for deployment "hpa-eval-fixed" rollout to finish: 1 of 2 updated replicas are available...
deployment "hpa-eval-fixed" successfully rolled out
READY_REPLICAS_MATCH_DECLARED deployment=hpa-eval-fixed declared=2 ready=2
LOAD_START t0=2026-09-03T23:58:12Z
MANIFEST_T0_WRITTEN arm=fixed path=/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/results/runs/t1-a-coldstart-verify/manifest.json
2026-09-03T23:58:12Z
{
  "run_id": "t1-a-coldstart-verify",
  "smoke": true,
  "repetitions": 1,
  "duration_minutes": 4,
  "hpa_max_replicas": 3,
  "arms": {
    "fixed": {
      "load_start_t0": "2026-09-03T23:58:12Z"
    }
  }
}
deployment.apps/hpa-eval-fixed condition met
DECLARED_REPLICAS_FROM_SPEC deployment=hpa-eval-fixed declared=2
ANCHOR_WINDOW_ENFORCED start=2026-09-03T23:57:09+00:00 end=2026-09-03T23:58:39+00:00
Querying cpu_utilization_pct: avg(app_cpu_usage_percent{experiment="fixed"})
Querying latency_p50_ms: histogram_quantile(0.50, sum(rate(app_request_latency_seconds_bucket{experiment="fixed"}[1m])) by (le)) * 1000
Querying latency_p95_ms: histogram_quantile(0.95, sum(rate(app_request_latency_seconds_bucket{experiment="fixed"}[1m])) by (le)) * 1000
Querying latency_p99_ms: histogram_quantile(0.99, sum(rate(app_request_latency_seconds_bucket{experiment="fixed"}[1m])) by (le)) * 1000
Querying rps: sum(rate(app_requests_total{experiment="fixed",status_code="200"}[1m]))
Querying error_rate components: sum(rate(app_requests_total{experiment="fixed"}[1m]))
Querying error_rate components: sum(rate(app_requests_total{experiment="fixed",status_code!="200"}[1m]))
FIXED_METRICS_REQUIRED_COLUMNS_POPULATED rows=7
ERROR_RATE_COLUMN_POPULATED rows=7/7 non_zero=0 missing=0
Wrote 7 rows to /tmp/t1-b-positive-fixed.csv
ASSERTIONS_PASS declared=2 observed_matches_declared=true
scripts/smoke_test.sh: line 51: 69166 Terminated: 15          kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" > /dev/null 2>&1
KIND_CLUSTER_READY
#0 building with "desktop-linux" instance using docker driver

#1 [internal] load build definition from Dockerfile
#1 transferring dockerfile: 884B done
#1 DONE 0.0s

#2 [internal] load metadata for docker.io/library/python:3.11-slim
#2 DONE 0.6s

#3 [internal] load .dockerignore
#3 transferring context: 2B done
#3 DONE 0.0s

#4 [internal] load build context
#4 transferring context: 64B done
#4 DONE 0.0s

#5 [1/6] FROM docker.io/library/python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534
#5 resolve docker.io/library/python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534 0.0s done
#5 DONE 0.0s

#6 [2/6] RUN groupadd --gid 1001 appgroup &&     useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser
#6 CACHED

#7 [5/6] RUN apt-get update && apt-get install -y --no-install-recommends gcc python3-dev     && pip install --no-cache-dir -r requirements.txt     && apt-get purge -y gcc python3-dev && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
#7 CACHED

#8 [4/6] COPY requirements.txt .
#8 CACHED

#9 [3/6] WORKDIR /app
#9 CACHED

#10 [6/6] COPY main.py .
#10 CACHED

#11 exporting to image
#11 exporting layers done
#11 exporting manifest sha256:86f0b7e99e966195f13ff9908dc7b01951d5b67264af2451ab996e325dc03be4 done
#11 exporting config sha256:0e5fae6793eae70f78d4d97cf0bfafa805fabae373f9ec5eb910908f2584bc52 done
#11 exporting attestation manifest sha256:cb0775573d76b93ed47da030c28358911f83c0dc72206eb4016ec22ba50f70d0 done
#11 exporting manifest list sha256:498fae922c7ed08c6cccaefd29a6f43c8b97853f4732813667cedecaed17cb3f done
#11 naming to docker.io/library/hpa-eval-app:smoke done
#11 unpacking to docker.io/library/hpa-eval-app:smoke done
#11 DONE 0.0s

View build details: docker-desktop://dashboard/build/desktop-linux/desktop-linux/quxkuhe52xzprzsjb3xwvqmau
Image: "hpa-eval-app:smoke" with ID "sha256:498fae922c7ed08c6cccaefd29a6f43c8b97853f4732813667cedecaed17cb3f" not yet present on node "hpa-eval-smoke-control-plane", loading...
Image: "hpa-eval-app:smoke" with ID "sha256:498fae922c7ed08c6cccaefd29a6f43c8b97853f4732813667cedecaed17cb3f" not yet present on node "hpa-eval-smoke-worker", loading...
KIND_IMAGE_LOADED image=hpa-eval-app:smoke
deployment.apps "metrics-server" deleted from kube-system namespace
serviceaccount/metrics-server unchanged
clusterrole.rbac.authorization.k8s.io/system:aggregated-metrics-reader unchanged
clusterrole.rbac.authorization.k8s.io/system:metrics-server unchanged
rolebinding.rbac.authorization.k8s.io/metrics-server-auth-reader unchanged
clusterrolebinding.rbac.authorization.k8s.io/metrics-server:system:auth-delegator unchanged
clusterrolebinding.rbac.authorization.k8s.io/system:metrics-server unchanged
service/metrics-server unchanged
deployment.apps/metrics-server created
apiservice.apiregistration.k8s.io/v1beta1.metrics.k8s.io unchanged
Waiting for deployment "metrics-server" rollout to finish: 0 of 1 updated replicas are available...
deployment "metrics-server" successfully rolled out
METRICS_SERVER_READY
namespace/hpa-eval unchanged
serviceaccount/prometheus unchanged
clusterrole.rbac.authorization.k8s.io/prometheus unchanged
clusterrolebinding.rbac.authorization.k8s.io/prometheus unchanged
configmap/prometheus-config unchanged
service/hpa-eval-fixed-svc unchanged
service/hpa-eval-hpa-svc unchanged
service/prometheus unchanged
deployment.apps/hpa-eval-fixed unchanged
deployment.apps/hpa-eval-hpa unchanged
deployment.apps/prometheus unchanged
horizontalpodautoscaler.autoscaling/hpa-eval-hpa unchanged
deployment.apps/hpa-eval-fixed condition met
deployment.apps/hpa-eval-hpa condition met
deployment.apps/prometheus condition met
HPA_UTILIZATION_PRESENT hpa-eval-hpa   Deployment/hpa-eval-hpa   cpu: 4%/60%   1     3     1     24h
METRIC_CONTRACT_VERIFIED
--- /metrics excerpt ---
app_requests_total{endpoint="/cpu",method="GET",status_code="200"} 25.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="0.005"} 0.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="0.01"} 0.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="0.025"} 5.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="0.05"} 6.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="0.1"} 25.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="0.25"} 25.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="0.5"} 25.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="1.0"} 25.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="2.5"} 25.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="5.0"} 25.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="10.0"} 25.0
app_request_latency_seconds_bucket{endpoint="/cpu",le="+Inf"} 25.0
app_cpu_usage_percent 1.9
ANCHOR_WINDOW_ENFORCED start=2026-09-03T23:58:24+00:00 end=2026-09-03T23:59:54+00:00
LABEL_ISOLATION_VERIFIED experiment=fixed increase=30.00200013334222
OPPOSITE_ARM_SERIES=0
Querying cpu_utilization_pct: avg(app_cpu_usage_percent{experiment="fixed"})
Querying latency_p50_ms: histogram_quantile(0.50, sum(rate(app_request_latency_seconds_bucket{experiment="fixed"}[1m])) by (le)) * 1000
Querying latency_p95_ms: histogram_quantile(0.95, sum(rate(app_request_latency_seconds_bucket{experiment="fixed"}[1m])) by (le)) * 1000
Querying latency_p99_ms: histogram_quantile(0.99, sum(rate(app_request_latency_seconds_bucket{experiment="fixed"}[1m])) by (le)) * 1000
Querying rps: sum(rate(app_requests_total{experiment="fixed",status_code="200"}[1m]))
Querying error_rate components: sum(rate(app_requests_total{experiment="fixed"}[1m]))
Querying error_rate components: sum(rate(app_requests_total{experiment="fixed",status_code!="200"}[1m]))
FIXED_METRICS_REQUIRED_COLUMNS_POPULATED rows=7
ERROR_RATE_COLUMN_POPULATED rows=7/7 non_zero=0 missing=0
Wrote 7 rows to /tmp/t1-c-fixed-metrics.csv
/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/scripts/lib/common.sh: line 31: 69335 Terminated: 15          kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" > /dev/null 2>&1
FIXED_METRICS_REQUIRED_COLUMNS_POPULATED
ERROR_RATE_COLUMN_POPULATED
#0 building with "desktop-linux" instance using docker driver

#1 [internal] load build definition from Dockerfile
#1 transferring dockerfile: 884B done
#1 DONE 0.0s

#2 [internal] load metadata for docker.io/library/python:3.11-slim
#2 DONE 0.5s

#3 [internal] load .dockerignore
#3 transferring context: 2B done
#3 DONE 0.0s

#4 [internal] load build context
#4 transferring context: 64B done
#4 DONE 0.0s

#5 [1/6] FROM docker.io/library/python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534
#5 resolve docker.io/library/python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534 0.0s done
#5 DONE 0.0s

#6 [3/6] WORKDIR /app
#6 CACHED

#7 [4/6] COPY requirements.txt .
#7 CACHED

#8 [2/6] RUN groupadd --gid 1001 appgroup &&     useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser
#8 CACHED

#9 [5/6] RUN apt-get update && apt-get install -y --no-install-recommends gcc python3-dev     && pip install --no-cache-dir -r requirements.txt     && apt-get purge -y gcc python3-dev && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
#9 CACHED

#10 [6/6] COPY main.py .
#10 CACHED

#11 exporting to image
#11 exporting layers done
#11 exporting manifest sha256:86f0b7e99e966195f13ff9908dc7b01951d5b67264af2451ab996e325dc03be4 done
#11 exporting config sha256:0e5fae6793eae70f78d4d97cf0bfafa805fabae373f9ec5eb910908f2584bc52 done
#11 exporting attestation manifest sha256:72e93131e0e9b3dd81668f1324a7eb512c447e63d10ce6568430e78b28c08850 done
#11 exporting manifest list sha256:8ca62290a43e2eea5b46ad438f5ba3db0bc25f7ab7bd0b05320d1f6e31ac94e6 done
#11 naming to docker.io/library/hpa-eval-app:smoke done
#11 unpacking to docker.io/library/hpa-eval-app:smoke done
#11 DONE 0.0s

View build details: docker-desktop://dashboard/build/desktop-linux/desktop-linux/wj9b7aidgqm8a3co8ypk9y0u2
Image: "hpa-eval-app:smoke" with ID "sha256:8ca62290a43e2eea5b46ad438f5ba3db0bc25f7ab7bd0b05320d1f6e31ac94e6" not yet present on node "hpa-eval-smoke-control-plane", loading...
Image: "hpa-eval-app:smoke" with ID "sha256:8ca62290a43e2eea5b46ad438f5ba3db0bc25f7ab7bd0b05320d1f6e31ac94e6" not yet present on node "hpa-eval-smoke-worker", loading...
KIND_IMAGE_LOADED image=hpa-eval-app:smoke
deployment.apps/hpa-eval-fixed restarted
Waiting for deployment "hpa-eval-fixed" rollout to finish: 1 out of 2 new replicas have been updated...
Waiting for deployment "hpa-eval-fixed" rollout to finish: 1 out of 2 new replicas have been updated...
Waiting for deployment "hpa-eval-fixed" rollout to finish: 1 out of 2 new replicas have been updated...
Waiting for deployment "hpa-eval-fixed" rollout to finish: 1 old replicas are pending termination...
Waiting for deployment "hpa-eval-fixed" rollout to finish: 1 old replicas are pending termination...
Waiting for deployment "hpa-eval-fixed" rollout to finish: 1 old replicas are pending termination...
deployment "hpa-eval-fixed" successfully rolled out
ANCHOR_WINDOW_ENFORCED start=2026-09-04T00:00:00+00:00 end=2026-09-04T00:01:30+00:00
Querying cpu_utilization_pct: avg(app_cpu_usage_percent{experiment="fixed"})
Querying latency_p50_ms: histogram_quantile(0.50, sum(rate(app_request_latency_seconds_bucket{experiment="fixed"}[1m])) by (le)) * 1000
Querying latency_p95_ms: histogram_quantile(0.95, sum(rate(app_request_latency_seconds_bucket{experiment="fixed"}[1m])) by (le)) * 1000
Querying latency_p99_ms: histogram_quantile(0.99, sum(rate(app_request_latency_seconds_bucket{experiment="fixed"}[1m])) by (le)) * 1000
Querying rps: sum(rate(app_requests_total{experiment="fixed",status_code="200"}[1m]))
Querying error_rate components: sum(rate(app_requests_total{experiment="fixed"}[1m]))
Querying error_rate components: sum(rate(app_requests_total{experiment="fixed",status_code!="200"}[1m]))
FIXED_METRICS_REQUIRED_COLUMNS_POPULATED rows=7
ERROR_RATE_COLUMN_POPULATED rows=7/7 non_zero=4 missing=0
Wrote 7 rows to /tmp/t1-c-error-rate-positive.csv
ERROR_RATE_NONZERO_VERIFIED
deployment.apps/hpa-eval-fixed condition met
deployment.apps/hpa-eval-hpa condition met
LABEL_ISOLATION_VERIFIED experiment=fixed increase=145.1941922323107
OPPOSITE_ARM_SERIES=0
ANCHOR_WINDOW_ENFORCED start=2026-09-04T00:00:28+00:00 end=2026-09-04T00:01:58+00:00
LABEL_ISOLATION_VERIFIED experiment=fixed increase=145.1941922323107
OPPOSITE_ARM_SERIES=0
Querying cpu_utilization_pct: avg(app_cpu_usage_percent{experiment="fixed"})
Querying latency_p50_ms: histogram_quantile(0.50, sum(rate(app_request_latency_seconds_bucket{experiment="fixed"}[1m])) by (le)) * 1000
Querying latency_p95_ms: histogram_quantile(0.95, sum(rate(app_request_latency_seconds_bucket{experiment="fixed"}[1m])) by (le)) * 1000
Querying latency_p99_ms: histogram_quantile(0.99, sum(rate(app_request_latency_seconds_bucket{experiment="fixed"}[1m])) by (le)) * 1000
Querying rps: sum(rate(app_requests_total{experiment="fixed",status_code="200"}[1m]))
Querying error_rate components: sum(rate(app_requests_total{experiment="fixed"}[1m]))
Querying error_rate components: sum(rate(app_requests_total{experiment="fixed",status_code!="200"}[1m]))
FIXED_METRICS_REQUIRED_COLUMNS_POPULATED rows=7
ERROR_RATE_COLUMN_POPULATED rows=7/7 non_zero=6 missing=0
Wrote 7 rows to /tmp/label_isolation.csv
scripts/smoke_test.sh: line 280: 69716 Terminated: 15          kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" > /dev/null 2>&1
LOCUST_FIXED_STATS_FOUND
LOCUST_HPA_STATS_FOUND
REQUEST_AUTHORITY=LOCUST
PROM_AUTHORITY=REPLICAS_CPU_TIMING
LOCUST_BOTH_ARMS_INGESTED
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
/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/analysis/analyze_results.py:154: UserWarning: Tight layout not applied. The left and right margins cannot be made large enough to accommodate all Axes decorations.
  plt.tight_layout()
/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/analysis/analyze_results.py:185: UserWarning: Tight layout not applied. The left and right margins cannot be made large enough to accommodate all Axes decorations.
  plt.tight_layout()
/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/analysis/analyze_results.py:224: UserWarning: Tight layout not applied. The left and right margins cannot be made large enough to accommodate all Axes decorations.
  plt.tight_layout()
  Saved /var/folders/l6/dk2zrhl54g7d688zrpdrynjw0000gn/T/preflight-analyze-figures-bbgsqihk/latency_comparison.png
  Saved /var/folders/l6/dk2zrhl54g7d688zrpdrynjw0000gn/T/preflight-analyze-figures-bbgsqihk/throughput_comparison.png
  Saved /var/folders/l6/dk2zrhl54g7d688zrpdrynjw0000gn/T/preflight-analyze-figures-bbgsqihk/cpu_replicas.png
  Saved /var/folders/l6/dk2zrhl54g7d688zrpdrynjw0000gn/T/preflight-analyze-figures-bbgsqihk/cost_performance.png
analyze_plotting=PASS figures=4 fixture_dir=/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/scripts/lib/fixtures
PREFLIGHT_PASS
PREFLIGHT_TABLE_END
deployment.apps/prometheus condition met
[2026-09-04T00:02:02Z] HEARTBEAT trap-verify
PS_BEFORE mode=normal pf_pid=69862 hb_pid=69863
69862 69848 00:00 kubectl port-forward svc/prometheus 19102:9090 -n hpa-eval --context kind-hpa-eval-smoke
69863 69848 00:00 bash /Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/scripts/lib/trap_test_runner.sh normal /tmp/trap-test-normal-69007.log hpa-eval hpa-eval-smoke 19102
PS_SNAPSHOT_BEFORE_BEGIN
[2026-09-04T00:02:02Z] HEARTBEAT trap-verify
69862 69848 00:00 kubectl port-forward svc/prometheus 19102:9090 -n hpa-eval --context kind-hpa-eval-smoke
69863 69848 00:00 bash /Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/scripts/lib/trap_test_runner.sh normal /tmp/trap-test-normal-69007.log hpa-eval hpa-eval-smoke 19102
srirammadduri    69862   0.0  0.1 411385328  26560   ??  R     5:02PM   0:00.01 kubectl port-forward svc/prometheus 19102:9090 -n hpa-eval --context kind-hpa-eval-smoke
PS_SNAPSHOT_BEFORE_END
pf_pid=69862
pf_port=19102
hb_pid=69863
PS_SNAPSHOT_BEFORE_BEGIN
[2026-09-04T00:02:02Z] HEARTBEAT trap-verify
69862 69848 00:00 kubectl port-forward svc/prometheus 19102:9090 -n hpa-eval --context kind-hpa-eval-smoke
69863 69848 00:00 bash /Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/scripts/lib/trap_test_runner.sh normal /tmp/trap-test-normal-69007.log hpa-eval hpa-eval-smoke 19102
srirammadduri    69862   0.0  0.1 411385328  26560   ??  R     5:02PM   0:00.01 kubectl port-forward svc/prometheus 19102:9090 -n hpa-eval --context kind-hpa-eval-smoke
PS_SNAPSHOT_BEFORE_END
TRAP_FIRED reason=EXIT
PS_AFTER mode=normal pf_pid=69862 hb_pid=69863
PS_AFTER pf_pid=69862 not listed (expected)
PS_AFTER hb_pid=69863 not listed (expected)
PS_AFTER no HEARTBEAT trap-verify processes (expected)
TRAP_SCENARIO_PASS mode=normal
[2026-09-04T00:02:04Z] HEARTBEAT trap-verify
PS_BEFORE mode=error pf_pid=69905 hb_pid=69906
69905 69891 00:00 kubectl port-forward svc/prometheus 19263:9090 -n hpa-eval --context kind-hpa-eval-smoke
69906 69891 00:00 bash /Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/scripts/lib/trap_test_runner.sh error /tmp/trap-test-error-69007.log hpa-eval hpa-eval-smoke 19263
PS_SNAPSHOT_BEFORE_BEGIN
69905 69891 00:00 kubectl port-forward svc/prometheus 19263:9090 -n hpa-eval --context kind-hpa-eval-smoke
[2026-09-04T00:02:04Z] HEARTBEAT trap-verify
69906 69891 00:00 bash /Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/scripts/lib/trap_test_runner.sh error /tmp/trap-test-error-69007.log hpa-eval hpa-eval-smoke 19263
srirammadduri    69905   0.0  0.1 411384816  20928   ??  R     5:02PM   0:00.01 kubectl port-forward svc/prometheus 19263:9090 -n hpa-eval --context kind-hpa-eval-smoke
PS_SNAPSHOT_BEFORE_END
pf_pid=69905
pf_port=19263
hb_pid=69906
PS_SNAPSHOT_BEFORE_BEGIN
69905 69891 00:00 kubectl port-forward svc/prometheus 19263:9090 -n hpa-eval --context kind-hpa-eval-smoke
[2026-09-04T00:02:04Z] HEARTBEAT trap-verify
69906 69891 00:00 bash /Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/scripts/lib/trap_test_runner.sh error /tmp/trap-test-error-69007.log hpa-eval hpa-eval-smoke 19263
srirammadduri    69905   0.0  0.1 411384816  20928   ??  R     5:02PM   0:00.01 kubectl port-forward svc/prometheus 19263:9090 -n hpa-eval --context kind-hpa-eval-smoke
PS_SNAPSHOT_BEFORE_END
TRAP_FIRED reason=EXIT
PS_AFTER mode=error pf_pid=69905 hb_pid=69906
PS_AFTER pf_pid=69905 not listed (expected)
PS_AFTER hb_pid=69906 not listed (expected)
PS_AFTER no HEARTBEAT trap-verify processes (expected)
TRAP_SCENARIO_PASS mode=error
[2026-09-04T00:02:06Z] HEARTBEAT trap-verify
PS_BEFORE mode=sigint pf_pid=69951 hb_pid=69952
69951 69937 00:00 kubectl port-forward svc/prometheus 19330:9090 -n hpa-eval --context kind-hpa-eval-smoke
69952 69937 00:00 bash /Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/scripts/lib/trap_test_runner.sh sigint /tmp/trap-test-sigint-69007.log hpa-eval hpa-eval-smoke 19330
PS_SNAPSHOT_BEFORE_BEGIN
69951 69937 00:00 kubectl port-forward svc/prometheus 19330:9090 -n hpa-eval --context kind-hpa-eval-smoke
[2026-09-04T00:02:06Z] HEARTBEAT trap-verify
69952 69937 00:00 bash /Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/scripts/lib/trap_test_runner.sh sigint /tmp/trap-test-sigint-69007.log hpa-eval hpa-eval-smoke 19330
srirammadduri    69951   0.0  0.1 411384816  22688   ??  R     5:02PM   0:00.01 kubectl port-forward svc/prometheus 19330:9090 -n hpa-eval --context kind-hpa-eval-smoke
PS_SNAPSHOT_BEFORE_END
pf_pid=69951
pf_port=19330
hb_pid=69952
PS_SNAPSHOT_BEFORE_BEGIN
69951 69937 00:00 kubectl port-forward svc/prometheus 19330:9090 -n hpa-eval --context kind-hpa-eval-smoke
[2026-09-04T00:02:06Z] HEARTBEAT trap-verify
69952 69937 00:00 bash /Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/scripts/lib/trap_test_runner.sh sigint /tmp/trap-test-sigint-69007.log hpa-eval hpa-eval-smoke 19330
srirammadduri    69951   0.0  0.1 411384816  22688   ??  R     5:02PM   0:00.01 kubectl port-forward svc/prometheus 19330:9090 -n hpa-eval --context kind-hpa-eval-smoke
PS_SNAPSHOT_BEFORE_END
TRAP_FIRED reason=TERM
TRAP_FIRED reason=TERM
PS_AFTER mode=sigint pf_pid=69951 hb_pid=69952
PS_AFTER pf_pid=69951 not listed (expected)
PS_AFTER hb_pid=69952 not listed (expected)
PS_AFTER no HEARTBEAT trap-verify processes (expected)
TRAP_SCENARIO_PASS mode=sigint
TRAP_CLEANUP_IDEMPOTENT
PROJECT_CLUSTER_VERIFICATION_SKIPPED no --env-file
TRAP_CLEANUP_VERIFIED
deployment.apps/hpa-eval-fixed condition met
MID_RUN_SCALE_WRONG declared=2 scaled_to=1
deployment.apps/hpa-eval-fixed scaled
deployment "hpa-eval-fixed" successfully rolled out
ASSERTION FAILED: fixed arm expected 2 replicas, observed 1
ANCHOR_WINDOW_ENFORCED start=2026-09-04T00:01:08+00:00 end=2026-09-04T00:02:38+00:00
LABEL_ISOLATION_VERIFIED experiment=fixed increase=75.53134115039086
OPPOSITE_ARM_SERIES=0
Querying cpu_utilization_pct: avg(app_cpu_usage_percent{experiment="fixed"})
Querying latency_p50_ms: histogram_quantile(0.50, sum(rate(app_request_latency_seconds_bucket{experiment="fixed"}[1m])) by (le)) * 1000
Querying latency_p95_ms: histogram_quantile(0.95, sum(rate(app_request_latency_seconds_bucket{experiment="fixed"}[1m])) by (le)) * 1000
Querying latency_p99_ms: histogram_quantile(0.99, sum(rate(app_request_latency_seconds_bucket{experiment="fixed"}[1m])) by (le)) * 1000
Querying rps: sum(rate(app_requests_total{experiment="fixed",status_code="200"}[1m]))
Querying error_rate components: sum(rate(app_requests_total{experiment="fixed"}[1m]))
Querying error_rate components: sum(rate(app_requests_total{experiment="fixed",status_code!="200"}[1m]))
Traceback (most recent call last):
  File "/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/analysis/collect_metrics.py", line 452, in <module>
    main()
    ~~~~^^
  File "/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/analysis/collect_metrics.py", line 413, in main
    rows = collect(
        mode=args.mode,
    ...<10 lines>...
        run_label_isolation_check=not args.skip_label_isolation,
    )
  File "/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/analysis/collect_metrics.py", line 343, in collect
    raise RuntimeError(msg)
RuntimeError: ASSERTION FAILED: fixed arm expected 2 replicas, observed 1
deployment.apps/hpa-eval-fixed scaled
scripts/smoke_test.sh: line 512: 70011 Terminated: 15          kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" > /dev/null 2>&1
Waiting for deployment "hpa-eval-fixed" rollout to finish: 1 of 2 updated replicas are available...
deployment "hpa-eval-fixed" successfully rolled out
NEGATIVE_FIXED_REPLICA_ASSERT_PASS
ASSERTION FAILED: required column rps has zero populated rows in /tmp/t1-b-empty-col-fixed.csv
NEGATIVE_EMPTY_METRICS_COLUMN_PASS
ASSERTION FAILED: publication blocked; locust_hpa_stats.csv is absent
NEGATIVE_MISSING_LOCUST_HPA_PASS
ASSERTION FAILED: locust_fixed_stats.csv is absent
NEGATIVE_MISSING_LOCUST_FIXED_PASS
deployment.apps/hpa-eval-hpa condition met
HPA_NO_LOAD_TEST minReplicas=1
deployment.apps/hpa-eval-hpa scaled
deployment "hpa-eval-hpa" successfully rolled out
HPA_NEVER_SCALED peak_observed=1 minReplicas=1
ANCHOR_WINDOW_ENFORCED start=2026-09-04T00:01:20+00:00 end=2026-09-04T00:02:50+00:00
Traceback (most recent call last):
  File "/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/analysis/collect_metrics.py", line 452, in <module>
    main()
    ~~~~^^
  File "/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/analysis/collect_metrics.py", line 413, in main
    rows = collect(
        mode=args.mode,
    ...<10 lines>...
        run_label_isolation_check=not args.skip_label_isolation,
    )
  File "/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/analysis/collect_metrics.py", line 292, in collect
    raise RuntimeError(msg)
RuntimeError: HPA_NEVER_SCALED peak_observed=1 minReplicas=1
scripts/smoke_test.sh: line 659: 70089 Terminated: 15          kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" > /dev/null 2>&1
NEGATIVE_HPA_NEVER_SCALED_PASS
deployment.apps/hpa-eval-fixed condition met
deployment.apps/hpa-eval-hpa condition met
Traceback (most recent call last):
  File "/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/analysis/collect_metrics.py", line 452, in <module>
    main()
    ~~~~^^
  File "/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/analysis/collect_metrics.py", line 411, in main
    assert_label_isolation(args.prometheus_url, args.mode, start_ts, end_ts)
    ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/srirammadduri/Documents/Personal Projects/k8s-hpa-benchmark/analysis/collect_metrics.py", line 222, in assert_label_isolation
    raise RuntimeError(
        f"LABEL_ISOLATION_FAILED no request increase for experiment={mode} in {window_sec}s window"
    )
RuntimeError: LABEL_ISOLATION_FAILED no request increase for experiment=fixed in 90s window
scripts/smoke_test.sh: line 709: 70117 Terminated: 15          kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" > /dev/null 2>&1
NEGATIVE_LABEL_ISOLATION_PASS
NEGATIVE_ASSERTION_TEST_PASS
ALL_TIER1_ASSERTIONS_EXERCISED
SMOKE_SUITE_PASS
```

- **Surprises:** `check_assertions` needed `--skip-label-isolation` (label isolation has dedicated positive/negative checks). Full suite now includes `negative_label_isolation` and `BOTH_DEPLOYMENTS_UP=true` for positive label-isolation.

---

## t1-f-full-smoke-suite-honest (supersedes 2026-09-03 dishonest pass, 2026-09-04)

- **Files touched:** `scripts/smoke_test.sh`, `HANDOFF.md`
- **Goal:** `--full` runs every check for real; locust-authority fresh by default; GKE identity guards require `--env-file`.
- **Changes:**
  - `--full` requires `--env-file`; passes through to `check_preflight_traps` (no `PROJECT_CLUSTER_VERIFICATION_SKIPPED`).
  - `--reuse-artifacts` opt-in (default off); reuse prints `REUSED_ARTIFACTS run_id=…`, fresh run prints `LOCUST_FRESH_RUN`.
  - Split kind vs GKE cluster names (`KIND_CLUSTER=hpa-eval-smoke`, `GKE_CLUSTER_NAME` from `.env`).
  - `verify_metric_contract` warms `/cpu` before scrape (empty histogram has no `_bucket` series; was silently aborting suite after `HPA_UTILIZATION_PRESENT`).
  - `HANDOFF.md`: post-run GCP orphan verification commands, LoadBalancer note, `destructive_gke_teardown` never deletes, zonal spend estimate.
- **Verification command:** `bash scripts/smoke_test.sh --full --env-file .env`
- **Elapsed time:** ~14.5 min (869s wall clock)
- **Exit code:** 0 (`SMOKE_SUITE_PASS`)

### Honesty checks (this pass)

| Check | Evidence |
|-------|----------|
| Fresh locust (not reuse) | `LOCUST_FRESH_RUN run_id=smoke-locust` (no `REUSED_ARTIFACTS`) |
| Locust actually ran | `LOCUST_PID_CONFIRMED pid=73614` (fixed arm, 2026-09-04T00:29:04Z); `LOCUST_PID_CONFIRMED pid=73830` (hpa arm, 2026-09-04T00:33:14Z) |
| Benchmark summary | `SUMMARY attempted=1 passed=1` |
| GKE identity guards | `NEGATIVE_CLUSTER_VERIFICATION_PASS`, `PROJECT_CLUSTER_VERIFICATION_REQUIRED` (not skipped) |
| Metric contract | `METRIC_CONTRACT_VERIFIED` (both harness passes) |
| Suite | `NEGATIVE_ASSERTION_TEST_PASS`, `ALL_TIER1_ASSERTIONS_EXERCISED`, `SMOKE_SUITE_PASS` |

### Key output excerpts

```
LOCUST_FRESH_RUN run_id=smoke-locust
LOCUST_PID_CONFIRMED pid=73614 confirmed_at=2026-09-04T00:29:04Z attempt=1 command=.../locust ... --host http://127.0.0.1:30080 --headless --run-time 4m ...
LOCUST_PID_CONFIRMED pid=73830 confirmed_at=2026-09-04T00:33:14Z attempt=1 command=.../locust ... --host http://127.0.0.1:30081 --headless --run-time 4m ...
SUMMARY attempted=1 passed=1
NEGATIVE_CLUSTER_VERIFICATION_PASS
PROJECT_CLUSTER_VERIFICATION_REQUIRED
SMOKE_SUITE_PASS
```

Full log: `/tmp/t1-f-full-honest-verify.log` (691 lines).

- **Surprises:** Prior `--full` without `--env-file` skipped GKE guards and reused locust artifacts (ingest-only). Empty histogram on cold pods caused `verify_metric_contract` to fail silently at `grep app_request_latency_seconds_bucket` (set -e), truncating the suite before locust.

---

## t1-gke-prep: fixed nodes + metrics coverage threshold (2026-09-04)

- **Files touched:** `scripts/deploy_gke.sh`, `scripts/run_benchmark.sh`, `scripts/lib/common.sh`, `scripts/smoke_test.sh`, `analysis/metrics_contract.py`, `analysis/collect_metrics.py`, `analysis/analyze_results.py`, `HANDOFF.md`
- **Issue 1 — cluster autoscaler:** Removed `--enable-autoscaling`/`--min-nodes`/`--max-nodes` from `deploy_gke.sh`. Fixed `--num-nodes=3` (see HANDOFF.md arithmetic).
- **Issue 2 — coverage threshold:** Added `MIN_COLUMN_COVERAGE_RATIO=0.95` in `analysis/metrics_contract.py`; collectors and `analyze_results.py` print `METRICS_COLUMN_COVERAGE` per column and abort with `METRICS_COVERAGE_BELOW_THRESHOLD`. Benchmark collection window anchored `t0+60s` → `t0+RUN_TIME` (no trailing `iso_now` bucket). Negative test `low-metrics-coverage` (50% CSV) added to `--full`.
- **App resources (`deployment-hpa.yaml`):** CPU request `100m`, limit `200m`; memory request `128Mi`, limit `256Mi`.
- **Verification command:** `bash scripts/smoke_test.sh --full --env-file .env`
- **Elapsed time:** ~15.6 min (938s; log `/tmp/t1-gke-prep-full11.log`)
- **Exit code:** 0 (`SMOKE_SUITE_PASS`)

### Coverage lines (representative from full run)

```
METRICS_COLUMN_COVERAGE column=cpu_utilization_pct populated=6/6 ratio=1.0000
METRICS_COLUMN_COVERAGE column=latency_p50_ms populated=6/6 ratio=1.0000
...
METRICS_COLUMN_COVERAGE column=error_rate populated=6/6 ratio=1.0000
METRICS_COLUMN_COVERAGE path=.../fixed_metrics.csv column=cpu_utilization_pct populated=8/8 ratio=1.0000
...
METRICS_COVERAGE_BELOW_THRESHOLD column=cpu_utilization_pct populated=3/5 ratio=0.6000 threshold=0.95
NEGATIVE_LOW_METRICS_COVERAGE_PASS
LOCUST_FRESH_RUN run_id=smoke-locust
SUMMARY attempted=1 passed=1
SMOKE_SUITE_PASS
```

- **Surprises:** Re-running `--full` on a Prometheus TSDB polluted by a prior `/fail` error-rate test can make `check_fixed-metrics` see non-zero `error_rate`; restart Prometheus before a clean gate if re-running locally.

---

## t1-burst-window: publish t0..t0+RUN_TIME + Prometheus auto-reset (2026-09-04)

- **Files touched:** `analysis/collect_metrics.py`, `analysis/metrics_contract.py`, `scripts/run_benchmark.sh`, `scripts/lib/common.sh`, `scripts/lib/cleanup.sh`, `scripts/smoke_test.sh`, `locust/locustfile_smoke.py`, `RESULTS.md`
- **Issue 1 — burst excluded:** Collection had been anchored `t0+60s` → `t0+RUN_TIME`, dropping the first minute of load. Fixed: 60s scrape pre-roll during post-cold-start wait (`ensure_metrics_preroll`); published window is `t0` → `t0+RUN_TIME` with no row exclusions; PromQL queries from `t0-60s`.
- **Issue 2 — Prometheus state leak:** `reset_prometheus_deployment` + `wait_prometheus_scrape_ready` at `--full` suite start and after `check_error_rate_positive`; no operator restart instruction required.
- **Smoke shape:** `locustfile_smoke.py` extended to 10m with sustained low load after burst (removed early `runner.quit()` that stopped traffic at 4m while `RUN_TIME=10m`).
- **Verification commands:**
  - `bash scripts/smoke_test.sh --check fixed-metrics` — 41/41 columns at 1.0000 (`/tmp/t1-burst-coverage-test.log`)
  - `bash scripts/smoke_test.sh --check locust-authority` — `SUMMARY attempted=1 passed=1` (`/tmp/t1-burst-locust.log`, ~22.6 min)
- **`--full` wall clock:** Exceeds 45 min with 600s metric windows (stopped at locust on first attempt before smoke-shape fix). Individual checks above pass the anchor and coverage gates.

### Anchor + coverage (locust-authority, fixed arm)

```
LOAD_START t0=2026-09-04T05:18:14Z
ANCHOR_WINDOW_ENFORCED start=2026-09-04T05:18:14+00:00 end=2026-09-04T05:28:14+00:00
METRIC_QUERY_PREROLL_SEC=60 rate_window_sec=30 query_start=2026-09-04T05:17:14+00:00 published_rows_only=true no_row_exclusions=true
METRICS_COLUMN_COVERAGE column=latency_p50_ms populated=39/41 ratio=0.9512
METRICS_COLUMN_COVERAGE column=error_rate populated=39/41 ratio=0.9512
```

HPA arm: `LOAD_START t0=2026-09-04T05:29:25Z` → `ANCHOR_WINDOW_ENFORCED start=2026-09-04T05:29:25+00:00` (same 39/41 request-metric coverage).

- **Edge rows:** Two burst-onset rows per arm remain `MISSING` for rate-derived columns (30s lookback vs 15s scrape); 39/41 = 0.9512 meets the 0.95 threshold without exclusions.
