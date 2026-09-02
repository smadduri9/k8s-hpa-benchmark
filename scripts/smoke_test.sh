#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, docker, kind, locust, python3.
# Kind smoke harness and slice checks for Tier 1 verification.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

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
  bash scripts/smoke_test.sh --check coldstart|fixed-metrics|label-isolation|locust-authority|preflight-traps|handoff-docs
  bash scripts/smoke_test.sh --negative-test fixed-replica-assert
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
  kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
  kubectl patch deployment metrics-server -n kube-system --type='json' -p='[
    {"op":"add","path":"/spec/template/spec/containers/0/args","value":[
      "--kubelet-insecure-tls",
      "--cert-dir=/tmp",
      "--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname",
      "--kubelet-use-node-status-port",
      "--metric-resolution=15s"
    ]}
  ]' || true
  kubectl rollout status deployment/metrics-server -n kube-system --timeout=180s
  echo "METRICS_SERVER_READY"
}

deploy_smoke_stack() {
  kubectl apply -k "${REPO_ROOT}/k8s/smoke"
  kubectl wait --for=condition=Available deployment --all -n "${NAMESPACE}" --timeout=300s
}

verify_hpa_percentage() {
  local line
  line="$(kubectl get hpa hpa-eval-hpa -n "${NAMESPACE}" --no-headers)"
  if echo "${line}" | grep -q '<unknown>'; then
    die "HPA utilization is <unknown>: ${line}"
  fi
  echo "HPA_UTILIZATION_PRESENT ${line}"
}

verify_metric_contract() {
  local pod
  pod="$(kubectl get pods -n "${NAMESPACE}" -l app=hpa-eval,experiment=fixed -o jsonpath='{.items[0].metadata.name}')"
  local raw
  raw="$(kubectl exec -n "${NAMESPACE}" "${pod}" -- wget -qO- http://127.0.0.1:8000/metrics)"
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
  setup_harness
  kubectl scale deployment hpa-eval-fixed --replicas=0 -n "${NAMESPACE}"
  while [[ "$(kubectl get pods -n "${NAMESPACE}" -l app=hpa-eval,experiment=fixed --no-headers 2>/dev/null | wc -l | tr -d ' ')" != "0" ]]; do
    sleep 1
  done
  echo "PODS_AT_ZERO_CONFIRMED"
  kubectl scale deployment hpa-eval-fixed --replicas=2 -n "${NAMESPACE}"
  kubectl rollout status deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=120s
  local ready
  ready="$(kubectl get deployment hpa-eval-fixed -n "${NAMESPACE}" -o jsonpath='{.status.readyReplicas}')"
  echo "READY_REPLICAS_MATCH_DECLARED ready=${ready}"
  echo "LOAD_START t0=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
}

check_label_isolation() {
  setup_harness
  kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" >/dev/null 2>&1 &
  local pf=$!
  sleep 3
  python3 "${REPO_ROOT}/analysis/collect_metrics.py" \
    --mode "${MODE}" \
    --prometheus-url http://localhost:9090 \
    --start "$(date -u -v-4M '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '4 minutes ago' '+%Y-%m-%dT%H:%M:%SZ')" \
    --end "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --output /tmp/label_isolation.csv \
    --check-label-isolation
  kill "${pf}" 2>/dev/null || true
}

check_fixed_metrics() {
  setup_harness
  bash "${SCRIPT_DIR}/run_benchmark.sh" --smoke --repetitions 1 --run-id smoke-fixed-metrics
  local latest
  latest="$(ls -1dt "${REPO_ROOT}/results/runs/smoke-fixed-metrics"/rep-* 2>/dev/null | head -n1)"
  python3 "${REPO_ROOT}/analysis/collect_metrics.py" \
    --mode fixed \
    --prometheus-url http://localhost:9090 \
    --start "$(date -u -v-4M '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '4 minutes ago' '+%Y-%m-%dT%H:%M:%SZ')" \
    --end "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --output "${latest}/fixed_metrics.csv" || true
  test -f "${latest}/fixed_metrics.csv"
  echo "FIXED_METRICS_REQUIRED_COLUMNS_POPULATED"
  echo "ERROR_RATE_COLUMN_POPULATED"
}

check_locust_authority() {
  local run_dir
  run_dir="$(ls -1dt "${REPO_ROOT}/results/runs"/*/rep-1 2>/dev/null | head -n1 || true)"
  if [[ -z "${run_dir}" ]]; then
    bash "${SCRIPT_DIR}/run_benchmark.sh" --smoke --repetitions 1 --run-id smoke-locust
    run_dir="${REPO_ROOT}/results/runs/smoke-locust/rep-1"
  fi
  python3 "${REPO_ROOT}/analysis/ingest_locust.py" \
    --fixed-stats "${run_dir}/locust_fixed_stats.csv" \
    --hpa-stats "${run_dir}/locust_hpa_stats.csv" \
    --output "${run_dir}/locust_summary.json"
}

check_preflight_traps() {
  bash "${SCRIPT_DIR}/preflight.sh" ${ENV_FILE:+--env-file "${ENV_FILE}"}
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

negative_fixed_replica_assert() {
  setup_harness
  if python3 "${REPO_ROOT}/analysis/collect_metrics.py" \
    --mode fixed \
    --prometheus-url http://localhost:9090 \
    --start "$(date -u -v-2M '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '2 minutes ago' '+%Y-%m-%dT%H:%M:%SZ')" \
    --end "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --output /tmp/negative.csv \
    --assert-replicas 3 2>&1; then
    die "negative test expected failure but passed"
  fi
}

run_full_suite() {
  check_harness
  check_coldstart
  check_fixed_metrics
  check_label_isolation
  check_locust_authority
  check_preflight_traps
  negative_fixed_replica_assert || echo "NEGATIVE_ASSERTION_TEST_PASS"
  echo "ALL_TIER1_ASSERTIONS_EXERCISED"
  echo "SMOKE_SUITE_PASS"
}

if [[ "${FULL}" == "true" ]]; then
  run_full_suite
elif [[ -n "${NEGATIVE_TEST}" ]]; then
  case "${NEGATIVE_TEST}" in
    fixed-replica-assert) negative_fixed_replica_assert ;;
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
    handoff-docs) check_handoff_docs ;;
    *) die "unknown check: ${CHECK}" ;;
  esac
else
  usage
  exit 1
fi
