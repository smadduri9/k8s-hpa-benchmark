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

ENV_FILE=""
CHECK=""
NEGATIVE_TEST=""
MODE="fixed"
BOTH_DEPLOYMENTS_UP=false
FULL=false
CLUSTER_NAME="${KIND_CLUSTER_NAME:-hpa-eval-smoke}"
IMAGE_NAME="hpa-eval-app:smoke"
NAMESPACE="hpa-eval"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/smoke_test.sh --check harness
  bash scripts/smoke_test.sh --check coldstart|assertions|fixed-metrics|label-isolation|locust-authority|preflight-traps|handoff-docs|error-rate-positive
  bash scripts/smoke_test.sh --negative-test fixed-replica-assert|empty-metrics-column|missing-locust-hpa|missing-locust-fixed|hpa-never-scaled|label-isolation|coldstart-readiness
  bash scripts/smoke_test.sh --full
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
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

load_env_file "${ENV_FILE}"
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
  kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" >/dev/null 2>&1 &
  local pf=$!
  sleep 3
  smoke_warm_fixed_traffic
  venv_python "${REPO_ROOT}/analysis/collect_metrics.py" \
    --mode "${MODE}" \
    --prometheus-url http://localhost:9090 \
    --start "$(date -u -v-90S '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '90 seconds ago' '+%Y-%m-%dT%H:%M:%SZ')" \
    --end "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --output /tmp/label_isolation.csv \
    --check-label-isolation
  kill "${pf}" 2>/dev/null || true
}

check_fixed_metrics() {
  if [[ "${BOTH_DEPLOYMENTS_UP}" == "true" ]]; then
    kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true
    kubectl wait --for=condition=Available deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=120s
  else
    setup_harness
  fi
  kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" >/dev/null 2>&1 &
  local pf=$!
  sleep 3
  smoke_warm_fixed_traffic
  local out_csv="/tmp/t1-c-fixed-metrics.csv"
  venv_python "${REPO_ROOT}/analysis/collect_metrics.py" \
    --mode fixed \
    --prometheus-url http://localhost:9090 \
    --start "$(date -u -v-90S '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '90 seconds ago' '+%Y-%m-%dT%H:%M:%SZ')" \
    --end "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --step 15 \
    --namespace "${NAMESPACE}" \
    --output "${out_csv}" \
    --assert-replicas "$(deployment_declared_replicas hpa-eval-fixed "${NAMESPACE}")"
  kill "${pf}" 2>/dev/null || true
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

  local traffic_pid
  (
    while true; do
      curl -sf "http://127.0.0.1:18080/cpu?intensity=low" >/dev/null 2>&1 || true
      curl -s -o /dev/null "http://127.0.0.1:18080/fail" || true
      sleep 1
    done
  ) &
  traffic_pid=$!
  sleep 75

  local out_csv="/tmp/t1-c-error-rate-positive.csv"
  local output
  output="$(venv_python "${REPO_ROOT}/analysis/collect_metrics.py" \
    --mode fixed \
    --prometheus-url http://localhost:9090 \
    --start "$(date -u -v-90S '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '90 seconds ago' '+%Y-%m-%dT%H:%M:%SZ')" \
    --end "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --step 15 \
    --namespace "${NAMESPACE}" \
    --output "${out_csv}" \
    --assert-replicas "$(deployment_declared_replicas hpa-eval-fixed "${NAMESPACE}")" \
    --skip-label-isolation 2>&1)"
  echo "${output}"

  kill "${traffic_pid}" 2>/dev/null || true
  wait "${traffic_pid}" 2>/dev/null || true
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
}

check_locust_authority() {
  local run_root="${REPO_ROOT}/results/runs/smoke-locust"
  local run_dir="${run_root}/rep-1"
  local status_file="${run_root}/STATUS"
  local needs_run=false
  if [[ ! -f "${run_dir}/locust_fixed_stats.csv" || ! -f "${run_dir}/locust_hpa_stats.csv" ]]; then
    needs_run=true
  elif [[ ! -f "${status_file}" ]] || ! head -n1 "${status_file}" | grep -qx "COMPLETE"; then
    needs_run=true
  else
    local fig
    for fig in latency_comparison.png throughput_comparison.png cpu_replicas.png cost_performance.png; do
      if [[ ! -f "${run_dir}/figures/${fig}" ]]; then
        needs_run=true
        break
      fi
    done
  fi

  if [[ "${needs_run}" == "true" ]]; then
    pkill -f 'HEARTBEAT locust-' 2>/dev/null || true
    rm -rf "${run_root}"
    bash "${SCRIPT_DIR}/run_benchmark.sh" --smoke --repetitions 1 --run-id smoke-locust || die "run_benchmark.sh failed for locust-authority"
    run_dir="${run_root}/rep-1"
  fi

  if [[ ! -f "${run_dir}/locust_fixed_stats.csv" || ! -f "${run_dir}/locust_hpa_stats.csv" ]]; then
    die "locust-authority check missing locust stats after benchmark run"
  fi
  if [[ ! -f "${status_file}" ]] || ! head -n1 "${status_file}" | grep -qx "COMPLETE"; then
    die "locust-authority STATUS not COMPLETE: $(cat "${status_file}" 2>/dev/null || echo MISSING)"
  fi
  local fig
  for fig in latency_comparison.png throughput_comparison.png cpu_replicas.png cost_performance.png; do
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
    require_env CLUSTER_NAME
    require_env REGION

    set +e
    local neg_output
    neg_output="$(CLUSTER_NAME="wrong-cluster-name-deliberate" \
      bash -c "source '${SCRIPT_DIR}/lib/common.sh'; source '${SCRIPT_DIR}/lib/cleanup.sh'; destructive_gke_teardown '${PROJECT_ID}' '${CLUSTER_NAME}' '${REGION}'" 2>&1)"
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

    destructive_gke_teardown "${PROJECT_ID}" "${CLUSTER_NAME}" "${REGION}"
    echo "PROJECT_CLUSTER_VERIFICATION_REQUIRED"
  else
    echo "PROJECT_CLUSTER_VERIFICATION_SKIPPED no --env-file"
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

check_assertions() {
  kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true
  kubectl wait --for=condition=Available deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=120s

  local declared
  declared="$(deployment_declared_replicas hpa-eval-fixed "${NAMESPACE}")"
  echo "DECLARED_REPLICAS_FROM_SPEC deployment=hpa-eval-fixed declared=${declared}"

  kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" >/dev/null 2>&1 &
  local pf=$!
  sleep 3

  smoke_warm_fixed_traffic

  venv_python "${REPO_ROOT}/analysis/collect_metrics.py" \
    --mode fixed \
    --prometheus-url http://localhost:9090 \
    --start "$(date -u -v-90S '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '90 seconds ago' '+%Y-%m-%dT%H:%M:%SZ')" \
    --end "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --step 15 \
    --output /tmp/t1-b-positive-fixed.csv \
    --assert-replicas "${declared}"

  kill "${pf}" 2>/dev/null || true
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

  set +e
  venv_python "${REPO_ROOT}/analysis/collect_metrics.py" \
    --mode fixed \
    --prometheus-url http://localhost:9090 \
    --start "$(date -u -v-90S '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '90 seconds ago' '+%Y-%m-%dT%H:%M:%SZ')" \
    --end "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --step 15 \
    --output /tmp/t1-b-negative-replica.csv \
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

  set +e
  local output
  output="$(venv_python "${REPO_ROOT}/analysis/collect_metrics.py" \
    --mode hpa \
    --prometheus-url http://localhost:9090 \
    --start "$(date -u -v-90S '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '90 seconds ago' '+%Y-%m-%dT%H:%M:%SZ')" \
    --end "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --step 15 \
    --namespace "${NAMESPACE}" \
    --output /tmp/t1-b-negative-hpa-never-scaled.csv \
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
    --start "$(date -u -v-90S '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '90 seconds ago' '+%Y-%m-%dT%H:%M:%SZ')" \
    --end "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
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
  check_harness
  check_coldstart
  check_assertions
  check_fixed_metrics
  check_error_rate_positive
  check_label_isolation
  check_locust_authority
  check_preflight_traps
  negative_fixed_replica_assert
  negative_empty_metrics_column
  negative_missing_locust_hpa
  negative_missing_locust_fixed
  negative_hpa_never_scaled
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
    missing-locust-hpa) negative_missing_locust_hpa ;;
    missing-locust-fixed) negative_missing_locust_fixed ;;
    hpa-never-scaled) negative_hpa_never_scaled ;;
    label-isolation) negative_label_isolation ;;
    coldstart-readiness) negative_coldstart_readiness ;;
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
    assertions) check_assertions ;;
    handoff-docs) check_handoff_docs ;;
    *) die "unknown check: ${CHECK}" ;;
  esac
else
  usage
  exit 1
fi
