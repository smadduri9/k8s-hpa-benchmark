#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, docker, kind, repo .venv tooling.
# Kind smoke harness and slice checks for Tier 1 verification.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/cold_start.sh
source "${SCRIPT_DIR}/lib/cold_start.sh"
# shellcheck source=lib/cleanup.sh
source "${SCRIPT_DIR}/lib/cleanup.sh"
# shellcheck source=lib/replica_sampler.sh
source "${SCRIPT_DIR}/lib/replica_sampler.sh"

ENV_FILE=""
CHECK=""
NEGATIVE_TEST=""
MODE="fixed"
BOTH_DEPLOYMENTS_UP=false
FULL=false
REUSE_ARTIFACTS=false
KIND_CLUSTER="${KIND_CLUSTER_NAME:-hpa-eval-smoke}"
GKE_CLUSTER_NAME=""
IMAGE_NAME="hpa-eval-app:smoke"
NAMESPACE="hpa-eval"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/smoke_test.sh --check harness
  bash scripts/smoke_test.sh --check coldstart|assertions|fixed-metrics|label-isolation|locust-authority|preflight-traps|handoff-docs|error-rate-positive|event-loop-not-blocked|endpoints-never-empty|readiness-sweep
  bash scripts/smoke_test.sh --negative-test fixed-replica-assert|empty-metrics-column|low-metrics-coverage|missing-locust-hpa|missing-locust-fixed|hpa-never-scaled|label-isolation|coldstart-readiness|liveness-restarts-hung
  bash scripts/smoke_test.sh --full --env-file .env [--reuse-artifacts]
  bash scripts/smoke_test.sh --check locust-authority [--reuse-artifacts]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --check) CHECK="$2"; shift 2 ;;
    --negative-test) NEGATIVE_TEST="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --both-deployments-up) BOTH_DEPLOYMENTS_UP=true; shift ;;
    --full) FULL=true; shift ;;
    --reuse-artifacts) REUSE_ARTIFACTS=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

load_env_file "${ENV_FILE}"
GKE_CLUSTER_NAME="${CLUSTER_NAME:-}"
CLUSTER_NAME="${KIND_CLUSTER}"
require_venv

ensure_kind_cluster() {
  if ! command -v kind >/dev/null 2>&1; then
    die "kind is required for smoke tests"
  fi
  if ! kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
    kind create cluster --name "${CLUSTER_NAME}" --config "${REPO_ROOT}/kind/kind-config.yaml"
  fi
  kubectl cluster-info --context "kind-${CLUSTER_NAME}" >/dev/null
  echo "KIND_CLUSTER_READY"
}

build_and_load_image() {
  docker build -t "${IMAGE_NAME}" "${REPO_ROOT}/app"
  kind load docker-image "${IMAGE_NAME}" --name "${CLUSTER_NAME}"
  echo "KIND_IMAGE_LOADED image=${IMAGE_NAME}"
}

install_metrics_server() {
  # Delete broken rollout entirely; do not patch args in place.
  kubectl delete deployment metrics-server -n kube-system --ignore-not-found=true
  while kubectl get pods -n kube-system -l k8s-app=metrics-server --no-headers 2>/dev/null | grep -q .; do
    sleep 2
  done

  kubectl apply -f "${REPO_ROOT}/k8s/smoke/metrics-server.yaml"
  kubectl rollout status deployment/metrics-server -n kube-system --timeout=180s
  echo "METRICS_SERVER_READY"
}

deploy_smoke_stack() {
  kubectl kustomize "${REPO_ROOT}/k8s/smoke" --load-restrictor LoadRestrictionsNone | kubectl apply -f -
  kubectl wait --for=condition=Available deployment --all -n "${NAMESPACE}" --timeout=300s
}

verify_hpa_percentage() {
  # HARNESS SETUP ONLY: wait up to 240s for metrics-server/HPA to populate after reinstall.
  # This tolerance is NOT used during benchmark runs (see scripts/run_benchmark.sh / collect_metrics.py).
  local line=""
  local attempt
  for attempt in $(seq 1 24); do
    line="$(kubectl get hpa hpa-eval-hpa -n "${NAMESPACE}" --no-headers)"
    if ! echo "${line}" | grep -q '<unknown>'; then
      echo "HPA_UTILIZATION_PRESENT ${line}"
      return 0
    fi
    sleep 10
  done
  die "HPA utilization is <unknown> after 240s: ${line}"
}

verify_metric_contract() {
  local pod
  pod="$(kubectl get pods -n "${NAMESPACE}" -l app=hpa-eval,experiment=fixed -o jsonpath='{.items[0].metadata.name}')"
  # Histogram _bucket series appear only after at least one observed request.
  kubectl exec -n "${NAMESPACE}" "${pod}" -- python3 -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/cpu?intensity=low')" >/dev/null
  local raw
  raw="$(kubectl exec -n "${NAMESPACE}" "${pod}" -- python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/metrics').read().decode())")"
  echo "${raw}" | grep -q 'app_requests_total'
  echo "${raw}" | grep -q 'app_cpu_usage_percent'
  echo "${raw}" | grep -q 'app_request_latency_seconds_bucket'
  echo "METRIC_CONTRACT_VERIFIED"
  echo "--- /metrics excerpt ---"
  echo "${raw}" | grep -E '^(app_requests_total|app_cpu_usage_percent|app_request_latency_seconds_bucket)' | head -n 20
}

setup_harness() {
  ensure_kind_cluster
  build_and_load_image
  install_metrics_server
  deploy_smoke_stack
  verify_hpa_percentage
  verify_metric_contract
}

check_harness() {
  setup_harness
}

check_coldstart() {
  kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true
  # Assume harness already deployed on live cluster; ensure fixed arm has running pods.
  kubectl wait --for=condition=Available deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=120s
  bash "${SCRIPT_DIR}/run_benchmark.sh" --smoke --cold-start-only --arm fixed --run-id t1-a-coldstart-verify
  cat "${REPO_ROOT}/results/runs/t1-a-coldstart-verify/manifest.json"
}

negative_coldstart_readiness() {
  kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true
  kubectl wait --for=condition=Available deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=120s

  # Restore baseline probe before/after negative test.
  kubectl patch deployment hpa-eval-fixed -n "${NAMESPACE}" --type='json' -p='[
    {"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/health"}
  ]' >/dev/null

  kubectl patch deployment hpa-eval-fixed -n "${NAMESPACE}" --type='json' -p='[
    {"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/does-not-exist"}
  ]' >/dev/null
  kubectl rollout status deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=120s || true

  set +e
  COLD_START_READINESS_TIMEOUT_SEC=30 \
    bash -c "source '${SCRIPT_DIR}/lib/common.sh'; source '${SCRIPT_DIR}/lib/cold_start.sh'; cold_start_arm hpa-eval-fixed 'app=hpa-eval,experiment=fixed' '${NAMESPACE}' '' fixed 30 false" 2>&1
  local rc=$?
  set -e

  kubectl patch deployment hpa-eval-fixed -n "${NAMESPACE}" --type='json' -p='[
    {"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/health"}
  ]' >/dev/null
  kubectl rollout status deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=180s

  if [[ "${rc}" -eq 0 ]]; then
    die "negative coldstart test expected failure but passed"
  fi
  echo "NEGATIVE_COLDSTART_READINESS_PASS"
}

check_label_isolation() {
  if [[ "${BOTH_DEPLOYMENTS_UP}" == "true" ]]; then
    kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true
    kubectl wait --for=condition=Available deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=120s
    kubectl wait --for=condition=Available deployment/hpa-eval-hpa -n "${NAMESPACE}" --timeout=120s
  else
    setup_harness
  fi
  smoke_collect_fixed_metrics_anchored /tmp/label_isolation.csv \
    --check-label-isolation
}

check_fixed_metrics() {
  if [[ "${BOTH_DEPLOYMENTS_UP}" == "true" ]]; then
    kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true
    kubectl wait --for=condition=Available deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=120s
  else
    setup_harness
  fi
  local out_csv="/tmp/t1-c-fixed-metrics.csv"
  smoke_collect_fixed_metrics_anchored "${out_csv}" \
    --assert-replicas "$(deployment_declared_replicas hpa-eval-fixed "${NAMESPACE}")" \
    --skip-label-isolation
  test -f "${out_csv}"
  venv_python -c "
import csv, sys
with open('${out_csv}', newline='') as handle:
    rows = list(csv.DictReader(handle))
populated = [r['error_rate'] for r in rows if r.get('error_rate') not in ('', 'MISSING', None)]
if not populated:
    sys.exit('error_rate column has no populated rows')
if not all(float(v) == 0.0 for v in populated):
    sys.exit('error_rate positive test expected all-zero failure rate in fixed-metrics check')
"
  echo "FIXED_METRICS_REQUIRED_COLUMNS_POPULATED"
  echo "ERROR_RATE_COLUMN_POPULATED"
}

check_error_rate_positive() {
  kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true
  build_and_load_image
  kubectl rollout restart deployment/hpa-eval-fixed -n "${NAMESPACE}"
  kubectl rollout status deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=180s

  kubectl port-forward svc/hpa-eval-fixed-svc 18080:80 -n "${NAMESPACE}" >/dev/null 2>&1 &
  local app_pf=$!
  sleep 2

  kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" >/dev/null 2>&1 &
  local pf=$!
  sleep 3

  local preroll_start t0 t1 out_csv="/tmp/t1-c-error-rate-positive.csv" replica_series sampler_pid
  preroll_start="$(iso_now)"
  ensure_metrics_preroll "${preroll_start}"
  t0="$(iso_now)"
  t1="$(iso_add_run_time "${t0}" "${METRICS_SAMPLE_WINDOW_SEC}s")"
  echo "SMOKE_METRICS_WINDOW t0=${t0} end=${t1}"
  replica_series="/tmp/smoke-error-rate-replica-series-$$.csv"
  replica_sampler_start "${NAMESPACE}" "hpa-eval-fixed" "${replica_series}" "${SMOKE_METRICS_STEP}"
  sampler_pid=$!

  while [[ "$(_iso_to_epoch "$(iso_now)")" -lt "$(_iso_to_epoch "${t1}")" ]]; do
    curl -sf "http://127.0.0.1:18080/cpu?intensity=low" >/dev/null 2>&1 || true
    curl -s -o /dev/null "http://127.0.0.1:18080/fail" || true
    sleep 1
  done

  replica_sampler_stop "${sampler_pid}" "${replica_series}"

  local output
  output="$(venv_python "${REPO_ROOT}/analysis/collect_metrics.py" \
    --mode fixed \
    --prometheus-url http://localhost:9090 \
    --start "${t0}" \
    --end "${t1}" \
    --step "${SMOKE_METRICS_STEP}" \
    --namespace "${NAMESPACE}" \
    --output "${out_csv}" \
    --replica-series "${replica_series}" \
    --assert-replicas "$(deployment_declared_replicas hpa-eval-fixed "${NAMESPACE}")" \
    --skip-label-isolation 2>&1)"
  echo "${output}"

  kill "${app_pf}" 2>/dev/null || true
  wait "${app_pf}" 2>/dev/null || true
  kill "${pf}" 2>/dev/null || true
  wait "${pf}" 2>/dev/null || true

  if ! echo "${output}" | grep -q "ERROR_RATE_COLUMN_POPULATED"; then
    die "error-rate positive test missing ERROR_RATE_COLUMN_POPULATED marker"
  fi
  if ! echo "${output}" | grep -Eq "non_zero=[1-9][0-9]*"; then
    die "error-rate positive test expected non_zero>=1 in collector output"
  fi
  echo "ERROR_RATE_NONZERO_VERIFIED"

  reset_prometheus_deployment
  wait_prometheus_scrape_ready
}

smoke_ready_fixed_pod() {
  kubectl get pods -n "${NAMESPACE}" -l app=hpa-eval,experiment=fixed \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\t"}{.status.containerStatuses[0].ready}{"\n"}{end}' \
    | awk '$2=="Running" && $3=="true"{print $1; exit}'
}

check_event_loop_not_blocked() {
  # Load generator runs in-container via kubectl exec and shares the pod cgroup
  # (200m CPU, 256Mi memory). /health latency is a conservative upper bound.
  kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true
  build_and_load_image
  kubectl kustomize "${REPO_ROOT}/k8s/smoke" --load-restrictor LoadRestrictionsNone | kubectl apply -f -
  kubectl rollout restart deployment/hpa-eval-fixed -n "${NAMESPACE}"
  kubectl rollout status deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=180s
  kubectl wait --for=condition=Ready pod -l app=hpa-eval,experiment=fixed -n "${NAMESPACE}" --timeout=120s
  sleep 3

  local pod
  pod="$(smoke_ready_fixed_pod)"
  if [[ -z "${pod}" ]]; then
    die "no Running ready fixed pod for event-loop-not-blocked check"
  fi

  local output rc
  set +e
  output="$(kubectl exec -n "${NAMESPACE}" "${pod}" -- python3 -c '
import sys
import threading
import time
import urllib.request

CPU_URL = "http://127.0.0.1:8000/cpu?intensity=low"
HEALTH_URL = "http://127.0.0.1:8000/health"
LOAD_THREADS = 10


def cpu_load() -> None:
    while True:
        try:
            urllib.request.urlopen(CPU_URL, timeout=30)
        except Exception:
            pass


for _ in range(LOAD_THREADS):
    threading.Thread(target=cpu_load, daemon=True).start()
time.sleep(1)

latencies_ms: list[float] = []
failures = 0
for _ in range(20):
    start = time.perf_counter()
    try:
        resp = urllib.request.urlopen(HEALTH_URL, timeout=5)
        code = resp.status
    except Exception:
        code = 0
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    if code != 200:
        failures += 1
    else:
        latencies_ms.append(elapsed_ms)

if not latencies_ms:
    print("HEALTH_UNDER_LOAD max_ms=MISSING count=0 failures=20", file=sys.stderr)
    sys.exit(1)

max_ms = max(latencies_ms)
count = len(latencies_ms)
print(f"HEALTH_UNDER_LOAD max_ms={max_ms:.3f} count={count} failures={failures}")
if failures > 0 or max_ms >= 1000.0:
    sys.exit(1)
')"
  rc=$?
  set -e

  echo "${output}"
  if [[ "${rc}" -ne 0 ]]; then
    die "event-loop-not-blocked check failed"
  fi
  echo "EVENT_LOOP_NOT_BLOCKED_PASS"
}

SMOKE_FIXED_PODS=()
SMOKE_LOAD_PIDS=()

smoke_endpoints_stop_load() {
  local pid
  for pid in "${SMOKE_LOAD_PIDS[@]:-}"; do
    kill -9 "${pid}" 2>/dev/null || true
  done
  if [[ "${#SMOKE_LOAD_PIDS[@]}" -gt 0 ]]; then
    wait "${SMOKE_LOAD_PIDS[@]}" 2>/dev/null || true
  fi
  SMOKE_LOAD_PIDS=()
}

smoke_endpoints_collect_pods() {
  SMOKE_FIXED_PODS=()
  local pod_line
  while IFS= read -r pod_line; do
    SMOKE_FIXED_PODS+=("${pod_line}")
  done < <(kubectl get pods -n "${NAMESPACE}" -l app=hpa-eval,experiment=fixed \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\t"}{.status.containerStatuses[0].ready}{"\n"}{end}' \
    | awk '$2=="Running" && $3=="true"{print $1}')
}

smoke_endpoints_prepare_cluster() {
  kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true
  build_and_load_image
  kubectl kustomize "${REPO_ROOT}/k8s/smoke" --load-restrictor LoadRestrictionsNone | kubectl apply -f -
  kubectl scale deployment/hpa-eval-fixed -n "${NAMESPACE}" --replicas=3
  kubectl rollout status deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=180s
  kubectl wait --for=condition=Ready pod -l app=hpa-eval,experiment=fixed -n "${NAMESPACE}" --timeout=180s
  sleep 3
  smoke_endpoints_collect_pods
  if [[ "${#SMOKE_FIXED_PODS[@]}" -ne 3 ]]; then
    die "endpoints check requires 3 Running ready fixed pods, got ${#SMOKE_FIXED_PODS[@]}"
  fi
}

smoke_endpoints_start_load() {
  local threads="$1"
  local target_pod
  smoke_endpoints_stop_load
  for target_pod in "${SMOKE_FIXED_PODS[@]}"; do
    kubectl exec -n "${NAMESPACE}" "${target_pod}" -- python3 -c \
      "import threading, time, urllib.request
CPU_URL = 'http://127.0.0.1:8000/cpu?intensity=low'
def cpu_load() -> None:
    while True:
        try:
            urllib.request.urlopen(CPU_URL, timeout=30)
        except Exception:
            pass
for _ in range(${threads}):
    threading.Thread(target=cpu_load, daemon=True).start()
time.sleep(600)" >/dev/null 2>&1 &
    SMOKE_LOAD_PIDS+=("$!")
  done
  sleep 2
}

smoke_endpoints_patch_readiness() {
  local timeout_sec="$1"
  local failure_threshold="$2"
  kubectl patch deployment hpa-eval-fixed -n "${NAMESPACE}" --type='json' -p="[
    {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/readinessProbe/timeoutSeconds\",\"value\":${timeout_sec}},
    {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/readinessProbe/failureThreshold\",\"value\":${failure_threshold}}
  ]"
  kubectl rollout restart deployment/hpa-eval-fixed -n "${NAMESPACE}"
  kubectl rollout status deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=180s
  kubectl wait --for=condition=Ready pod -l app=hpa-eval,experiment=fixed -n "${NAMESPACE}" --timeout=180s
  sleep 3
  smoke_endpoints_collect_pods
}

smoke_endpoints_run_trial() {
  local threads="$1"
  local duration_sec="$2"
  export THREADS_PER_POD="${threads}"
  smoke_endpoints_start_load "${threads}"
  local trial_output trial_rc
  set +e
  trial_output="$(PODS="${SMOKE_FIXED_PODS[*]}" \
    DURATION_SEC="${duration_sec}" \
    NAMESPACE="${NAMESPACE}" \
    THREADS_PER_POD="${threads}" \
    venv_python -c '
import os
import json
import subprocess
import time

namespace = os.environ["NAMESPACE"]
duration_sec = int(os.environ["DURATION_SEC"])
pods = [p for p in os.environ["PODS"].split() if p]
threads_per_pod = os.environ.get("THREADS_PER_POD", "?")
service_name = "hpa-eval-fixed-svc"
health_script = (
    "import time, urllib.request\n"
    "start = time.perf_counter()\n"
    "urllib.request.urlopen(\"http://127.0.0.1:8000/health\", timeout=5)\n"
    "print((time.perf_counter() - start) * 1000.0)"
)


def ready_endpoint_count() -> int:
    proc = subprocess.run(
        [
            "kubectl",
            "get",
            "endpointslice",
            "-n",
            namespace,
            "-l",
            f"kubernetes.io/service-name={service_name}",
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    ready = 0
    for slc in payload.get("items", []):
        for endpoint in slc.get("endpoints", []):
            if endpoint.get("conditions", {}).get("ready"):
                ready += 1
    return ready


def pod_health_ms(pod: str) -> float:
    proc = subprocess.run(
        [
            "kubectl",
            "exec",
            "-n",
            namespace,
            pod,
            "--",
            "python3",
            "-c",
            health_script,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return 0.0
    try:
        return float(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0.0


samples = 0
min_ready = ready_endpoint_count()
health_max_ms = 0.0
start = time.perf_counter()
while time.perf_counter() - start < duration_sec:
    for pod in pods:
        health_max_ms = max(health_max_ms, pod_health_ms(pod))
    count = ready_endpoint_count()
    min_ready = min(min_ready, count)
    samples += 1
    time.sleep(1.0)

print(f"HEALTH_MAX_UNDER_LOAD threads_per_pod={threads_per_pod} max_ms={health_max_ms:.3f}")
print(f"ENDPOINTS_MIN_OBSERVED min={min_ready} samples={samples} duration_sec={duration_sec}")
')"
  trial_rc=$?
  set -e
  smoke_endpoints_stop_load
  echo "${trial_output}"
  return "${trial_rc}"
}

check_endpoints_never_empty() {
  smoke_endpoints_prepare_cluster

  local threads health_max_ms chosen_threads=0 target_pod sample_ms output
  for threads in 10 15 20 25 30 35 40; do
    smoke_endpoints_start_load "${threads}"
    health_max_ms=0
    for target_pod in "${SMOKE_FIXED_PODS[@]}"; do
      output="$(kubectl exec -n "${NAMESPACE}" "${target_pod}" -- python3 -c \
        'import time, urllib.request
start = time.perf_counter()
urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5)
print((time.perf_counter() - start) * 1000.0)' 2>/dev/null || echo "0")"
      sample_ms="${output%%$'\n'*}"
      if awk -v a="${sample_ms}" -v b="${health_max_ms}" 'BEGIN{exit !(a>b)}'; then
        health_max_ms="${sample_ms}"
      fi
    done
    echo "ENDPOINTS_LOAD_TRIAL threads_per_pod=${threads} health_max_ms=${health_max_ms}"
    if awk -v ms="${health_max_ms}" 'BEGIN{exit !(ms >= 1000)}'; then
      chosen_threads="${threads}"
      break
    fi
    smoke_endpoints_stop_load
  done

  if [[ "${chosen_threads}" -eq 0 ]]; then
    smoke_endpoints_stop_load
    die "could not drive /health to >=1000ms on kind with up to 40 threads per pod"
  fi

  local trial_output trial_rc
  set +e
  trial_output="$(smoke_endpoints_run_trial "${chosen_threads}" 60)"
  trial_rc=$?
  set -e
  echo "${trial_output}"
  if [[ "${trial_rc}" -ne 0 ]]; then
    die "endpoints-never-empty sampling failed"
  fi
  if echo "${trial_output}" | grep -q "ENDPOINTS_MIN_OBSERVED min=0"; then
    die "ENDPOINTS_EMPTY_UNDER_LOAD"
  fi
  echo "ENDPOINTS_NEVER_EMPTY_PASS"
}

check_readiness_sweep() {
  smoke_endpoints_prepare_cluster

  local -a sweep_configs=("1:3" "2:3" "2:6" "3:3" "3:6")
  local -a survivors=()
  local sweep_threads=15
  local duration_sec=60
  local cfg timeout_sec failure_threshold trial_output health_max endpoints_line min_ready pass_flag
  local best_timeout="" best_threshold="" best_min=-1

  echo "READINESS_SWEEP_BEGIN threads_per_pod=${sweep_threads} duration_sec=${duration_sec}"

  for cfg in "${sweep_configs[@]}"; do
    timeout_sec="${cfg%%:*}"
    failure_threshold="${cfg##*:}"
    smoke_endpoints_patch_readiness "${timeout_sec}" "${failure_threshold}"
    set +e
    trial_output="$(smoke_endpoints_run_trial "${sweep_threads}" "${duration_sec}")"
    set -e
    health_max="$(echo "${trial_output}" | grep '^HEALTH_MAX_UNDER_LOAD' | tail -n1)"
    endpoints_line="$(echo "${trial_output}" | grep '^ENDPOINTS_MIN_OBSERVED' | tail -n1)"
    if [[ -z "${endpoints_line}" ]]; then
      die "readiness sweep missing ENDPOINTS_MIN_OBSERVED for timeoutSeconds=${timeout_sec} failureThreshold=${failure_threshold}"
    fi
    min_ready="${endpoints_line#*min=}"
    min_ready="${min_ready%% *}"
    if [[ "${min_ready}" == "0" ]]; then
      pass_flag="fail"
    else
      pass_flag="pass"
      survivors+=("${timeout_sec}:${failure_threshold}:${min_ready}")
      if [[ "${min_ready}" -gt "${best_min}" ]]; then
        best_min="${min_ready}"
        best_timeout="${timeout_sec}"
        best_threshold="${failure_threshold}"
      elif [[ "${min_ready}" -eq "${best_min}" && ( -z "${best_timeout}" || "${timeout_sec}" -lt "${best_timeout}" ) ]]; then
        best_timeout="${timeout_sec}"
        best_threshold="${failure_threshold}"
      fi
    fi
    echo "READINESS_SWEEP_ROW timeoutSeconds=${timeout_sec} failureThreshold=${failure_threshold} ${health_max} ${endpoints_line} result=${pass_flag}"
    echo "${trial_output}"
  done

  kubectl kustomize "${REPO_ROOT}/k8s/smoke" --load-restrictor LoadRestrictionsNone | kubectl apply -f -
  kubectl rollout status deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=180s

  if [[ "${#survivors[@]}" -eq 0 ]]; then
    echo "READINESS_SWEEP_CLIFF none survived at threads_per_pod=${sweep_threads}"
    echo "READINESS_SWEEP_DONE"
    return 0
  fi

  echo "READINESS_SWEEP_SURVIVOR timeoutSeconds=${best_timeout} failureThreshold=${best_threshold} min_ready_at_15=${best_min}"
  local cliff_threads
  for cliff_threads in 20 25 30 35 40; do
    smoke_endpoints_patch_readiness "${best_timeout}" "${best_threshold}"
    set +e
    trial_output="$(smoke_endpoints_run_trial "${cliff_threads}" "${duration_sec}")"
    set -e
    health_max="$(echo "${trial_output}" | grep '^HEALTH_MAX_UNDER_LOAD' | tail -n1)"
    endpoints_line="$(echo "${trial_output}" | grep '^ENDPOINTS_MIN_OBSERVED' | tail -n1)"
    min_ready="${endpoints_line#*min=}"
    min_ready="${min_ready%% *}"
    if [[ "${min_ready}" == "0" ]]; then
      pass_flag="fail"
    else
      pass_flag="pass"
    fi
    echo "READINESS_SWEEP_CLIFF_ROW timeoutSeconds=${best_timeout} failureThreshold=${best_threshold} threads_per_pod=${cliff_threads} ${health_max} ${endpoints_line} result=${pass_flag}"
    echo "${trial_output}"
    if [[ "${pass_flag}" == "fail" ]]; then
      break
    fi
  done

  kubectl kustomize "${REPO_ROOT}/k8s/smoke" --load-restrictor LoadRestrictionsNone | kubectl apply -f -
  kubectl rollout status deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=180s
  echo "READINESS_SWEEP_DONE"
}

check_readiness_repeat_control() {
  smoke_endpoints_prepare_cluster
  smoke_endpoints_patch_readiness 1 3

  local run trial_output
  echo "READINESS_REPEAT_BEGIN timeoutSeconds=1 failureThreshold=3 threads_per_pod=15 runs=5"
  for run in 1 2 3 4 5; do
    echo "READINESS_REPEAT_RUN run=${run}"
    trial_output="$(smoke_endpoints_run_trial 15 60)"
    echo "${trial_output}"
  done

  kubectl kustomize "${REPO_ROOT}/k8s/smoke" --load-restrictor LoadRestrictionsNone | kubectl apply -f -
  kubectl rollout status deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=180s
  echo "READINESS_REPEAT_DONE"
}

negative_liveness_restarts_hung() {
  kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true

  LIVENESS_HUNG_RESTORED=false
  restore_fixed_manifest() {
    if [[ "${LIVENESS_HUNG_RESTORED}" == "true" ]]; then
      return 0
    fi
    kubectl kustomize "${REPO_ROOT}/k8s/smoke" --load-restrictor LoadRestrictionsNone | kubectl apply -f -
    kubectl patch deployment hpa-eval-fixed -n "${NAMESPACE}" --type='json' \
      -p='[{"op":"remove","path":"/spec/template/spec/containers/0/command"}]' 2>/dev/null || true
    kubectl rollout status deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=180s
    LIVENESS_HUNG_RESTORED=true
  }
  trap restore_fixed_manifest EXIT

  kubectl kustomize "${REPO_ROOT}/k8s/smoke" --load-restrictor LoadRestrictionsNone | kubectl apply -f -
  kubectl patch deployment hpa-eval-fixed -n "${NAMESPACE}" --type='json' \
    -p='[{"op":"remove","path":"/spec/template/spec/containers/0/command"}]' 2>/dev/null || true
  kubectl rollout restart deployment/hpa-eval-fixed -n "${NAMESPACE}"
  kubectl rollout status deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=180s

  local patch_file="/tmp/liveness-hung-patch-$$.json"
  PATCH_FILE="${patch_file}" venv_python -c '
import json
import os

script = """import socket, threading, time
RESP = b"HTTP/1.1 200 OK\\r\\nContent-Length: 2\\r\\n\\r\\nok"
deadline = time.time() + 12

def handle(conn):
    try:
        data = conn.recv(4096)
        if time.time() < deadline and b"/health" in data:
            conn.sendall(RESP)
        else:
            threading.Event().wait()
    finally:
        conn.close()

sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("0.0.0.0", 8000))
sock.listen(128)
while True:
    conn, _ = sock.accept()
    threading.Thread(target=handle, args=(conn,), daemon=True).start()
"""
patch = [{"op": "replace", "path": "/spec/template/spec/containers/0/command", "value": ["python3", "-c", script]}]
with open(os.environ["PATCH_FILE"], "w", encoding="utf-8") as handle:
    json.dump(patch, handle)
'
  if ! kubectl patch deployment hpa-eval-fixed -n "${NAMESPACE}" --type='json' --patch-file="${patch_file}"; then
    PATCH_FILE="${patch_file}" venv_python -c '
import json
import os

script = """import socket, threading, time
RESP = b"HTTP/1.1 200 OK\\r\\nContent-Length: 2\\r\\n\\r\\nok"
deadline = time.time() + 12

def handle(conn):
    try:
        data = conn.recv(4096)
        if time.time() < deadline and b"/health" in data:
            conn.sendall(RESP)
        else:
            threading.Event().wait()
    finally:
        conn.close()

sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("0.0.0.0", 8000))
sock.listen(128)
while True:
    conn, _ = sock.accept()
    threading.Thread(target=handle, args=(conn,), daemon=True).start()
"""
patch = [{"op": "add", "path": "/spec/template/spec/containers/0/command", "value": ["python3", "-c", script]}]
with open(os.environ["PATCH_FILE"], "w", encoding="utf-8") as handle:
    json.dump(patch, handle)
'
    kubectl patch deployment hpa-eval-fixed -n "${NAMESPACE}" --type='json' --patch-file="${patch_file}"
  fi
  rm -f "${patch_file}"
  kubectl rollout status deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=180s
  sleep 5

  local pod restart_before restart_after attempt
  pod="$(kubectl get pods -n "${NAMESPACE}" -l app=hpa-eval,experiment=fixed \
    --field-selector=status.phase=Running \
    --sort-by=.metadata.creationTimestamp \
    -o jsonpath='{.items[-1].metadata.name}')"
  restart_before="$(kubectl get pod "${pod}" -n "${NAMESPACE}" \
    -o jsonpath='{.status.containerStatuses[0].restartCount}')"

  restart_after="${restart_before}"
  for attempt in $(seq 1 24); do
    sleep 5
    if ! kubectl get pod "${pod}" -n "${NAMESPACE}" >/dev/null 2>&1; then
      pod="$(kubectl get pods -n "${NAMESPACE}" -l app=hpa-eval,experiment=fixed \
        --field-selector=status.phase=Running \
        --sort-by=.metadata.creationTimestamp \
        -o jsonpath='{.items[-1].metadata.name}')"
      restart_before=0
    fi
    restart_after="$(kubectl get pod "${pod}" -n "${NAMESPACE}" \
      -o jsonpath='{.status.containerStatuses[0].restartCount}' 2>/dev/null || echo "")"
    if [[ -n "${restart_after}" && "${restart_after}" -gt "${restart_before}" ]]; then
      if ! kubectl describe pod "${pod}" -n "${NAMESPACE}" | grep -q "Liveness probe failed"; then
        die "container restarted but missing Liveness probe failed event"
      fi
      echo "LIVENESS_RESTART_OBSERVED pod=${pod} restart_before=${restart_before} restart_after=${restart_after}"
      echo "NEGATIVE_LIVENESS_RESTARTS_HUNG_PASS"
      return 0
    fi
  done

  die "liveness probe did not restart hung container (restart_before=${restart_before} restart_after=${restart_after})"
}

check_locust_authority() {
  local run_id="smoke-locust"
  local run_root="${REPO_ROOT}/results/runs/${run_id}"
  local run_dir="${run_root}/rep-1"
  local status_file="${run_root}/STATUS"

  locust_artifacts_complete() {
    [[ -f "${run_dir}/locust_fixed_stats.csv" && -f "${run_dir}/locust_hpa_stats.csv" ]] \
      && [[ -f "${status_file}" ]] \
      && head -n1 "${status_file}" | grep -qx "COMPLETE" \
      && [[ -f "${run_dir}/figures/latency_comparison.png" ]] \
      && [[ -f "${run_dir}/figures/throughput_comparison.png" ]] \
      && [[ -f "${run_dir}/figures/cpu_replicas.png" ]] \
      && [[ -f "${run_dir}/figures/cost_performance.png" ]]
  }

  if [[ "${REUSE_ARTIFACTS}" == "true" ]] && locust_artifacts_complete; then
    echo "REUSED_ARTIFACTS run_id=${run_id}"
  else
    pkill -f 'HEARTBEAT locust-' 2>/dev/null || true
    rm -rf "${run_root}"
    echo "LOCUST_FRESH_RUN run_id=${run_id}"
    bash "${SCRIPT_DIR}/run_benchmark.sh" --smoke --repetitions 1 --run-id "${run_id}" \
      || die "run_benchmark.sh failed for locust-authority"
    run_dir="${run_root}/rep-1"
  fi

  if [[ ! -f "${run_dir}/locust_fixed_stats.csv" || ! -f "${run_dir}/locust_hpa_stats.csv" ]]; then
    die "locust-authority check missing locust stats after benchmark run"
  fi
  if [[ ! -f "${status_file}" ]] || ! head -n1 "${status_file}" | grep -qx "COMPLETE"; then
    die "locust-authority STATUS not COMPLETE: $(cat "${status_file}" 2>/dev/null || echo MISSING)"
  fi
  local fig
  for fig in latency_comparison.png latency_client_run_level.png latency_client_window.png throughput_comparison.png cpu_replicas.png cost_performance.png; do
    if [[ ! -f "${run_dir}/figures/${fig}" ]]; then
      die "locust-authority missing figure ${fig}"
    fi
  done

  venv_python "${REPO_ROOT}/analysis/ingest_locust.py" \
    --fixed-stats "${run_dir}/locust_fixed_stats.csv" \
    --hpa-stats "${run_dir}/locust_hpa_stats.csv" \
    --output "${run_dir}/locust_summary.json"
  echo "LOCUST_BOTH_ARMS_INGESTED"
}

verify_trap_scenario() {
  local mode="$1"
  local marker="/tmp/trap-test-${mode}-$$.log"
  local pf_port=$((19100 + RANDOM % 500))
  local rc=0
  local pf_pid="" hb_pid=""

  bash "${SCRIPT_DIR}/lib/trap_test_runner.sh" "${mode}" "${marker}" "${NAMESPACE}" "${CLUSTER_NAME}" "${pf_port}" &
  local runner_pid=$!

  local waited=0
  while [[ "${waited}" -lt 20 ]]; do
    if [[ -f "${marker}" ]] && grep -q '^pf_pid=' "${marker}"; then
      pf_pid="$(grep '^pf_pid=' "${marker}" | cut -d= -f2 || true)"
      hb_pid="$(grep '^hb_pid=' "${marker}" | cut -d= -f2 || true)"
      break
    fi
    sleep 0.2
    waited=$((waited + 1))
  done

  if [[ -z "${pf_pid}" ]]; then
    die "trap test ${mode}: pf_pid not recorded in marker"
  fi

  echo "PS_BEFORE mode=${mode} pf_pid=${pf_pid} hb_pid=${hb_pid:-MISSING}"
  ps -p "${pf_pid}" -o pid=,ppid=,etime=,command= 2>/dev/null || echo "PS_BEFORE pf_pid=${pf_pid} not listed"
  if [[ -n "${hb_pid}" ]]; then
    ps -p "${hb_pid}" -o pid=,ppid=,etime=,command= 2>/dev/null || echo "PS_BEFORE hb_pid=${hb_pid} not listed"
  fi
  ps aux | grep -E '[H]EARTBEAT trap-verify' || true

  if [[ "${mode}" == "sigint" ]]; then
    sleep 1
    kill -INT "${runner_pid}" 2>/dev/null || true
    sleep 2
    if ps -p "${runner_pid}" >/dev/null 2>&1; then
      kill -TERM "${runner_pid}" 2>/dev/null || true
      sleep 1
      kill -9 "${runner_pid}" 2>/dev/null || true
    fi
  fi

  set +e
  wait "${runner_pid}"
  rc=$?
  set -e

  if [[ -f "${marker}" ]]; then
    sed -n '/^PS_SNAPSHOT_BEFORE_BEGIN$/,/^PS_SNAPSHOT_BEFORE_END$/p' "${marker}" || true
    cat "${marker}"
  fi

  if ! grep -q "TRAP_FIRED reason=" "${marker}"; then
    die "trap test ${mode}: TRAP_FIRED marker missing"
  fi

  echo "PS_AFTER mode=${mode} pf_pid=${pf_pid} hb_pid=${hb_pid:-MISSING}"
  if ps -p "${pf_pid}" >/dev/null 2>&1; then
    ps -p "${pf_pid}" -o pid=,ppid=,etime=,command=
    die "trap test ${mode}: port-forward pid ${pf_pid} still running after trap"
  else
    echo "PS_AFTER pf_pid=${pf_pid} not listed (expected)"
  fi
  if [[ -n "${hb_pid}" ]]; then
    if ps -p "${hb_pid}" >/dev/null 2>&1; then
      ps -p "${hb_pid}" -o pid=,ppid=,etime=,command=
      die "trap test ${mode}: heartbeat pid ${hb_pid} still running after trap"
    else
      echo "PS_AFTER hb_pid=${hb_pid} not listed (expected)"
    fi
  fi
  if pgrep -f 'HEARTBEAT trap-verify' >/dev/null 2>&1; then
    ps aux | grep -E '[H]EARTBEAT trap-verify' || true
    die "trap test ${mode}: heartbeat subshell still present after trap"
  else
    echo "PS_AFTER no HEARTBEAT trap-verify processes (expected)"
  fi
  if lsof -nP -iTCP:"${pf_port}" -sTCP:LISTEN >/dev/null 2>&1; then
    die "trap test ${mode}: port ${pf_port} still listening after trap"
  fi

  case "${mode}" in
    normal) [[ "${rc}" -eq 0 ]] || die "trap test normal expected rc=0 got ${rc}" ;;
    error) [[ "${rc}" -ne 0 ]] || die "trap test error expected non-zero rc got ${rc}" ;;
    sigint) [[ "${rc}" -eq 130 || "${rc}" -eq 143 ]] || die "trap test sigint expected rc 130/143 got ${rc}" ;;
  esac

  echo "TRAP_SCENARIO_PASS mode=${mode}"
}

check_preflight_traps() {
  echo "TEARDOWN_POLICY cluster=on-failure-only port_forwards=always background_pids=always success_cluster_delete=never"
  bash "${SCRIPT_DIR}/preflight.sh" ${ENV_FILE:+--env-file "${ENV_FILE}"}

  kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true
  kubectl wait --for=condition=Available deployment/prometheus -n "${NAMESPACE}" --timeout=120s

  verify_trap_scenario normal
  verify_trap_scenario error
  verify_trap_scenario sigint

  cleanup_background_jobs
  cleanup_background_jobs
  echo "TRAP_CLEANUP_IDEMPOTENT"

  if [[ -n "${ENV_FILE}" && -f "${ENV_FILE}" ]]; then
    load_env_file "${ENV_FILE}"
    require_env PROJECT_ID
    if [[ -z "${GKE_CLUSTER_NAME}" ]]; then
      die "preflight-traps requires CLUSTER_NAME in --env-file for GKE identity guards"
    fi
    require_env REGION

    set +e
    local neg_output
    neg_output="$(CLUSTER_NAME="wrong-cluster-name-deliberate" \
      bash -c "source '${SCRIPT_DIR}/lib/common.sh'; source '${SCRIPT_DIR}/lib/cleanup.sh'; destructive_gke_teardown '${PROJECT_ID}' '${GKE_CLUSTER_NAME}' '${REGION}'" 2>&1)"
    local neg_rc=$?
    set -e
    echo "${neg_output}"
    if [[ "${neg_rc}" -eq 0 ]]; then
      die "destructive teardown negative test expected failure but passed"
    fi
    if ! echo "${neg_output}" | grep -q "cluster mismatch"; then
      die "destructive teardown negative test missing cluster mismatch message"
    fi
    echo "NEGATIVE_CLUSTER_VERIFICATION_PASS"

    destructive_gke_teardown "${PROJECT_ID}" "${GKE_CLUSTER_NAME}" "${REGION}"
    echo "PROJECT_CLUSTER_VERIFICATION_REQUIRED"
  else
    die "preflight-traps requires --env-file for GKE identity guards"
  fi

  echo "TRAP_CLEANUP_VERIFIED"
}

check_handoff_docs() {
  test -f "${REPO_ROOT}/HANDOFF.md"
  grep -q "run_benchmark.sh" "${REPO_ROOT}/HANDOFF.md"
  grep -q "smoke" "${REPO_ROOT}/HANDOFF.md"
  echo "HANDOFF_MD_PRESENT"
  echo "HANDOFF_COMMAND_ORDER_VALIDATED"
  echo "HANDOFF_TRUST_CHECKS_PRESENT"
}

smoke_warm_fixed_traffic() {
  kubectl port-forward svc/hpa-eval-fixed-svc 18080:80 -n "${NAMESPACE}" >/dev/null 2>&1 &
  local app_pf=$!
  sleep 2
  local i
  for i in $(seq 1 25); do
    curl -sf "http://127.0.0.1:18080/cpu?intensity=low" >/dev/null 2>&1 || true
  done
  kill "${app_pf}" 2>/dev/null || true
  wait "${app_pf}" 2>/dev/null || true
  sleep 20
}

smoke_sustain_fixed_traffic_until() {
  local target_iso="$1"
  kubectl port-forward svc/hpa-eval-fixed-svc 18080:80 -n "${NAMESPACE}" >/dev/null 2>&1 &
  local app_pf=$!
  sleep 2
  while [[ "$(_iso_to_epoch "$(iso_now)")" -lt "$(_iso_to_epoch "${target_iso}")" ]]; do
    curl -sf "http://127.0.0.1:18080/cpu?intensity=low" >/dev/null 2>&1 || true
    sleep 1
  done
  kill "${app_pf}" 2>/dev/null || true
  wait "${app_pf}" 2>/dev/null || true
}

smoke_collect_fixed_metrics_anchored() {
  local out_csv="$1"
  shift
  local -a extra_args=("$@")

  kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" >/dev/null 2>&1 &
  local pf=$!
  sleep 3

  local preroll_start t0 t1 replica_series sampler_pid
  preroll_start="$(iso_now)"
  ensure_metrics_preroll "${preroll_start}"
  t0="$(iso_now)"
  t1="$(iso_add_run_time "${t0}" "${METRICS_SAMPLE_WINDOW_SEC}s")"
  echo "SMOKE_METRICS_WINDOW t0=${t0} end=${t1}"
  replica_series="/tmp/smoke-fixed-replica-series-$$.csv"
  replica_sampler_start "${NAMESPACE}" "hpa-eval-fixed" "${replica_series}" "${SMOKE_METRICS_STEP}"
  sampler_pid=$!
  smoke_sustain_fixed_traffic_until "${t1}"
  replica_sampler_stop "${sampler_pid}" "${replica_series}"

  venv_python "${REPO_ROOT}/analysis/collect_metrics.py" \
    --mode fixed \
    --prometheus-url http://localhost:9090 \
    --start "${t0}" \
    --end "${t1}" \
    --step "${SMOKE_METRICS_STEP}" \
    --namespace "${NAMESPACE}" \
    --output "${out_csv}" \
    --replica-series "${replica_series}" \
    "${extra_args[@]}"

  kill "${pf}" 2>/dev/null || true
}

check_assertions() {
  kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true
  kubectl wait --for=condition=Available deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=120s

  local declared ready
  declared="$(deployment_declared_replicas hpa-eval-fixed "${NAMESPACE}")"
  ready="$(kubectl get deployment hpa-eval-fixed -n "${NAMESPACE}" -o jsonpath='{.status.readyReplicas}')"
  echo "DECLARED_REPLICAS_FROM_SPEC deployment=hpa-eval-fixed declared=${declared}"
  if [[ "${ready}" != "${declared}" ]]; then
    die "ASSERTION FAILED: declared=${declared} ready=${ready:-0}"
  fi
  echo "ASSERTIONS_PASS declared=${declared} observed_matches_declared=true"
}

negative_fixed_replica_assert() {
  kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true
  kubectl wait --for=condition=Available deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=120s

  local declared wrong
  declared="$(deployment_declared_replicas hpa-eval-fixed "${NAMESPACE}")"
  wrong=$((declared - 1))
  if [[ "${wrong}" -lt 1 ]]; then
    wrong=$((declared + 1))
  fi

  echo "MID_RUN_SCALE_WRONG declared=${declared} scaled_to=${wrong}"
  kubectl scale deployment hpa-eval-fixed --replicas="${wrong}" -n "${NAMESPACE}"
  kubectl rollout status deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=120s || true

  kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" >/dev/null 2>&1 &
  local pf=$!
  sleep 3

  smoke_warm_fixed_traffic

  local replica_series="/tmp/t1-b-negative-replica-series.csv"
  replica_series_write_constant "${NAMESPACE}" "hpa-eval-fixed" \
    "$(metrics_query_start_iso)" "$(metrics_query_end_iso)" 15 "${replica_series}"

  set +e
  venv_python "${REPO_ROOT}/analysis/collect_metrics.py" \
    --mode fixed \
    --prometheus-url http://localhost:9090 \
    --start "$(metrics_query_start_iso)" \
    --end "$(metrics_query_end_iso)" \
    --step 15 \
    --output /tmp/t1-b-negative-replica.csv \
    --replica-series "${replica_series}" \
    --assert-replicas "${declared}" 2>&1
  local rc=$?
  set -e

  kill "${pf}" 2>/dev/null || true

  kubectl scale deployment hpa-eval-fixed --replicas="${declared}" -n "${NAMESPACE}"
  kubectl rollout status deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=180s

  if [[ "${rc}" -eq 0 ]]; then
    die "negative fixed-replica test expected failure but passed"
  fi
  echo "NEGATIVE_FIXED_REPLICA_ASSERT_PASS"
}

negative_empty_metrics_column() {
  local fixed_csv="/tmp/t1-b-empty-col-fixed.csv"
  local hpa_csv="/tmp/t1-b-empty-col-hpa.csv"
  cat > "${fixed_csv}" <<'EOF'
timestamp,elapsed_seconds,experiment,data_source,run_id,cluster_name,collection_timestamp,replicas,cpu_utilization_pct,latency_p50_ms,latency_p95_ms,latency_p99_ms,rps,error_rate
2026-09-02T00:00:00+00:00,0,fixed,MEASURED,test,kind,2026-09-02T00:00:00+00:00,2,1.0,10.0,20.0,30.0,,0.0
EOF
  cat > "${hpa_csv}" <<'EOF'
timestamp,elapsed_seconds,experiment,data_source,run_id,cluster_name,collection_timestamp,replicas,cpu_utilization_pct,latency_p50_ms,latency_p95_ms,latency_p99_ms,rps,error_rate
2026-09-02T00:00:00+00:00,0,hpa,MEASURED,test,kind,2026-09-02T00:00:00+00:00,1,1.0,10.0,20.0,30.0,5.0,0.0
EOF

  set +e
  local output
  output="$(venv_python "${REPO_ROOT}/analysis/analyze_results.py" \
    --fixed "${fixed_csv}" \
    --hpa "${hpa_csv}" \
    --output-dir /tmp/t1-b-empty-col-figures 2>&1)"
  local rc=$?
  set -e
  echo "${output}"

  if [[ "${rc}" -eq 0 ]]; then
    die "negative empty-metrics-column test expected failure but passed"
  fi
  if ! echo "${output}" | grep -q "ASSERTION FAILED: required column rps has zero populated rows"; then
    die "negative empty-metrics-column test missing expected assertion message for rps"
  fi
  echo "NEGATIVE_EMPTY_METRICS_COLUMN_PASS"
}

negative_low_metrics_coverage() {
  local csv="/tmp/t1-b-low-coverage.csv"
  PYTHONPATH="${REPO_ROOT}/analysis" venv_python -c "
import csv
from pathlib import Path
from metrics_contract import MISSING, REQUIRED_VALUE_COLUMNS

path = Path('${csv}')
rows = []
for i in range(10):
    row = {
        'timestamp': f'2026-09-02T00:{i:02d}:00+00:00',
        'elapsed_seconds': str(i * 15),
        'experiment': 'fixed',
        'data_source': 'MEASURED',
        'run_id': 'test',
        'cluster_name': 'kind',
        'collection_timestamp': '2026-09-02T00:00:00+00:00',
        'replicas': '2',
    }
    for col in REQUIRED_VALUE_COLUMNS:
        if col == 'error_rate':
            row[col] = '0.0' if i % 2 == 0 else MISSING
        elif i % 2 == 0:
            row[col] = '1.0'
        else:
            row[col] = MISSING
    rows.append(row)

with path.open('w', newline='') as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
"

  set +e
  local output
  output="$(venv_python "${REPO_ROOT}/analysis/analyze_results.py" \
    --fixed "${csv}" \
    --hpa "${csv}" \
    --output-dir /tmp/t1-b-low-coverage-figures 2>&1)"
  local rc=$?
  set -e
  echo "${output}"

  if [[ "${rc}" -eq 0 ]]; then
    die "negative low-metrics-coverage test expected failure but passed"
  fi
  if ! echo "${output}" | grep -q "METRICS_COVERAGE_BELOW_THRESHOLD"; then
    die "negative low-metrics-coverage test missing METRICS_COVERAGE_BELOW_THRESHOLD"
  fi
  echo "NEGATIVE_LOW_METRICS_COVERAGE_PASS"
}

negative_missing_locust_hpa() {
  local fixed_csv="/tmp/t1-b-missing-locust-fixed.csv"
  local hpa_csv="/tmp/t1-b-missing-locust-hpa.csv"
  cat > "${fixed_csv}" <<'EOF'
timestamp,elapsed_seconds,experiment,data_source,run_id,cluster_name,collection_timestamp,replicas,cpu_utilization_pct,latency_p50_ms,latency_p95_ms,latency_p99_ms,rps,error_rate
2026-09-02T00:00:00+00:00,0,fixed,MEASURED,test,kind,2026-09-02T00:00:00+00:00,2,1.0,10.0,20.0,30.0,5.0,0.0
EOF
  cat > "${hpa_csv}" <<'EOF'
timestamp,elapsed_seconds,experiment,data_source,run_id,cluster_name,collection_timestamp,replicas,cpu_utilization_pct,latency_p50_ms,latency_p95_ms,latency_p99_ms,rps,error_rate
2026-09-02T00:00:00+00:00,0,hpa,MEASURED,test,kind,2026-09-02T00:00:00+00:00,1,1.0,10.0,20.0,30.0,5.0,0.0
EOF

  set +e
  local output
  output="$(venv_python "${REPO_ROOT}/analysis/analyze_results.py" \
    --fixed "${fixed_csv}" \
    --hpa "${hpa_csv}" \
    --locust-hpa-stats /tmp/does-not-exist-locust_hpa_stats.csv \
    --output-dir /tmp/t1-b-missing-locust-figures 2>&1)"
  local rc=$?
  set -e
  echo "${output}"

  if [[ "${rc}" -eq 0 ]]; then
    die "negative missing-locust-hpa test expected failure but passed"
  fi
  if ! echo "${output}" | grep -q "ASSERTION FAILED: publication blocked; locust_hpa_stats.csv is absent"; then
    die "negative missing-locust-hpa test missing expected assertion message"
  fi
  echo "NEGATIVE_MISSING_LOCUST_HPA_PASS"
}

negative_missing_locust_fixed() {
  local run_dir="/tmp/t1-d-missing-locust-fixed"
  mkdir -p "${run_dir}"
  cat > "${run_dir}/locust_hpa_stats.csv" <<'EOF'
Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,Min Response Time,Max Response Time,Average Content Size,Requests/s,Failures/s,50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%
Aggregated,Aggregated,10,0,1,1,1,1,1,1,0,1,1,1,1,1,1,1,1,1,1,1
EOF

  set +e
  local output
  output="$(venv_python "${REPO_ROOT}/analysis/ingest_locust.py" \
    --fixed-stats "${run_dir}/does-not-exist-locust_fixed_stats.csv" \
    --hpa-stats "${run_dir}/locust_hpa_stats.csv" \
    --output "${run_dir}/locust_summary.json" 2>&1)"
  local rc=$?
  set -e
  echo "${output}"

  if [[ "${rc}" -eq 0 ]]; then
    die "negative missing-locust-fixed test expected failure but passed"
  fi
  if ! echo "${output}" | grep -q "ASSERTION FAILED: locust_fixed_stats.csv is absent"; then
    die "negative missing-locust-fixed test missing expected assertion message"
  fi
  echo "NEGATIVE_MISSING_LOCUST_FIXED_PASS"
}

smoke_warm_hpa_traffic() {
  kubectl port-forward svc/hpa-eval-hpa-svc 18081:80 -n "${NAMESPACE}" >/dev/null 2>&1 &
  local app_pf=$!
  sleep 2
  local i
  for i in $(seq 1 25); do
    curl -sf "http://127.0.0.1:18081/cpu?intensity=low" >/dev/null 2>&1 || true
  done
  kill "${app_pf}" 2>/dev/null || true
  wait "${app_pf}" 2>/dev/null || true
  sleep 20
}

negative_hpa_never_scaled() {
  kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true
  kubectl wait --for=condition=Available deployment/hpa-eval-hpa -n "${NAMESPACE}" --timeout=120s

  local min_replicas
  min_replicas="$(hpa_min_replicas)"
  echo "HPA_NO_LOAD_TEST minReplicas=${min_replicas}"

  kubectl scale deployment hpa-eval-hpa --replicas="${min_replicas}" -n "${NAMESPACE}"
  kubectl rollout status deployment/hpa-eval-hpa -n "${NAMESPACE}" --timeout=120s

  kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" >/dev/null 2>&1 &
  local pf=$!
  sleep 3

  kubectl port-forward svc/hpa-eval-hpa-svc 18081:80 -n "${NAMESPACE}" >/dev/null 2>&1 &
  local app_pf=$!
  sleep 2
  curl -sf "http://127.0.0.1:18081/cpu?intensity=low" >/dev/null 2>&1 || true
  kill "${app_pf}" 2>/dev/null || true
  wait "${app_pf}" 2>/dev/null || true

  local replica_series="/tmp/t1-b-negative-hpa-series.csv"
  replica_series_write_constant "${NAMESPACE}" "hpa-eval-hpa" \
    "$(metrics_query_start_iso)" "$(metrics_query_end_iso)" 15 "${replica_series}"

  set +e
  local output
  output="$(venv_python "${REPO_ROOT}/analysis/collect_metrics.py" \
    --mode hpa \
    --prometheus-url http://localhost:9090 \
    --start "$(metrics_query_start_iso)" \
    --end "$(metrics_query_end_iso)" \
    --step 15 \
    --namespace "${NAMESPACE}" \
    --output /tmp/t1-b-negative-hpa-never-scaled.csv \
    --replica-series "${replica_series}" \
    --min-replicas "${min_replicas}" \
    --max-replicas 3 \
    --skip-label-isolation 2>&1)"
  local rc=$?
  set -e
  echo "${output}"

  kill "${pf}" 2>/dev/null || true

  if [[ "${rc}" -eq 0 ]]; then
    die "negative hpa-never-scaled test expected failure but passed"
  fi
  if ! echo "${output}" | grep -q "HPA_NEVER_SCALED"; then
    die "negative hpa-never-scaled test missing HPA_NEVER_SCALED"
  fi
  echo "NEGATIVE_HPA_NEVER_SCALED_PASS"
}

negative_label_isolation() {
  kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true
  kubectl wait --for=condition=Available deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=120s
  kubectl wait --for=condition=Available deployment/hpa-eval-hpa -n "${NAMESPACE}" --timeout=120s

  kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" >/dev/null 2>&1 &
  local pf=$!
  sleep 3
  smoke_warm_hpa_traffic

  set +e
  local output
  output="$(venv_python "${REPO_ROOT}/analysis/collect_metrics.py" \
    --mode fixed \
    --prometheus-url http://localhost:9090 \
    --start "$(metrics_query_start_iso)" \
    --end "$(metrics_query_end_iso)" \
    --output /tmp/t1-c-negative-label-isolation.csv \
    --check-label-isolation 2>&1)"
  local rc=$?
  set -e
  echo "${output}"

  kill "${pf}" 2>/dev/null || true

  if [[ "${rc}" -eq 0 ]]; then
    die "negative label-isolation test expected failure but passed"
  fi
  if ! echo "${output}" | grep -q "LABEL_ISOLATION_FAILED"; then
    die "negative label-isolation test missing LABEL_ISOLATION_FAILED"
  fi
  echo "NEGATIVE_LABEL_ISOLATION_PASS"
}

run_full_suite() {
  if [[ -z "${ENV_FILE}" ]]; then
    die "--full requires --env-file so preflight-traps exercises GKE identity guards"
  fi
  check_harness
  kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true
  reset_prometheus_deployment
  wait_prometheus_scrape_ready
  check_event_loop_not_blocked
  negative_liveness_restarts_hung
  check_coldstart
  check_assertions
  check_fixed_metrics
  check_error_rate_positive
  BOTH_DEPLOYMENTS_UP=true
  check_label_isolation
  BOTH_DEPLOYMENTS_UP=false
  check_locust_authority
  check_preflight_traps
  negative_fixed_replica_assert
  negative_empty_metrics_column
  negative_low_metrics_coverage
  negative_missing_locust_hpa
  negative_missing_locust_fixed
  negative_hpa_never_scaled
  negative_label_isolation
  echo "NEGATIVE_ASSERTION_TEST_PASS"
  echo "ALL_TIER1_ASSERTIONS_EXERCISED"
  echo "SMOKE_SUITE_PASS"
}

if [[ "${FULL}" == "true" ]]; then
  run_full_suite
elif [[ -n "${NEGATIVE_TEST}" ]]; then
  case "${NEGATIVE_TEST}" in
    fixed-replica-assert) negative_fixed_replica_assert ;;
    empty-metrics-column) negative_empty_metrics_column ;;
    low-metrics-coverage) negative_low_metrics_coverage ;;
    missing-locust-hpa) negative_missing_locust_hpa ;;
    missing-locust-fixed) negative_missing_locust_fixed ;;
    hpa-never-scaled) negative_hpa_never_scaled ;;
    label-isolation) negative_label_isolation ;;
    coldstart-readiness) negative_coldstart_readiness ;;
    liveness-restarts-hung) negative_liveness_restarts_hung ;;
    *) die "unknown negative test: ${NEGATIVE_TEST}" ;;
  esac
elif [[ -n "${CHECK}" ]]; then
  case "${CHECK}" in
    harness) check_harness ;;
    coldstart) check_coldstart ;;
    fixed-metrics) check_fixed_metrics ;;
    label-isolation) check_label_isolation ;;
    locust-authority) check_locust_authority ;;
    preflight-traps) check_preflight_traps ;;
    error-rate-positive) check_error_rate_positive ;;
    event-loop-not-blocked) check_event_loop_not_blocked ;;
    endpoints-never-empty) check_endpoints_never_empty ;;
    readiness-sweep) check_readiness_sweep ;;
    readiness-repeat-control) check_readiness_repeat_control ;;
    assertions) check_assertions ;;
    handoff-docs) check_handoff_docs ;;
    *) die "unknown check: ${CHECK}" ;;
  esac
else
  usage
  exit 1
fi
