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
