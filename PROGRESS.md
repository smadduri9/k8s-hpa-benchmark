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

- **Files touched:** `scripts/run_benchmark.sh`, `scripts/lib/common.sh`
- **Verification command:** `bash scripts/smoke_test.sh --check coldstart` (pending Docker)
- **Actual output:** cold-start helpers implemented (`PODS_AT_ZERO_CONFIRMED`, `READY_REPLICAS_MATCH_DECLARED`, `LOAD_START t0=`)
- **Elapsed time:** ~45 min
- **Surprises:** none

---

## t1-b-assertions

- **Files touched:** `analysis/collect_metrics.py`, `analysis/analyze_results.py`, `scripts/run_benchmark.sh`
- **Verification command:** `bash scripts/smoke_test.sh --negative-test fixed-replica-assert` (pending Docker)
- **Actual output:** assertion messages implemented in collector (`ASSERTION FAILED: fixed arm expected N replicas, observed M`)
- **Elapsed time:** ~35 min
- **Surprises:** none

---

## t1-c-fixed-metrics

- **Files touched:** `analysis/collect_metrics.py`
- **Verification command:** `bash scripts/smoke_test.sh --check fixed-metrics` and `--check label-isolation` (pending Docker)
- **Actual output:** experiment-scoped PromQL, anchored `--start/--end`, kubectl replica sampling, strict series cardinality, MISSING semantics
- **Elapsed time:** ~70 min
- **Surprises:** `app_requests_total` only increments on `/cpu`; `/` health checks are not counted (metric contract implication for error_rate).

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
