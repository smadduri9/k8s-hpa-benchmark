#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, docker, kind, locust, python3.
# Idempotent benchmark runner with cold-start enforcement, smoke mode, and per-repetition status.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/cleanup.sh
source "${SCRIPT_DIR}/lib/cleanup.sh"

ENV_FILE=""
SMOKE=false
REPETITIONS=1
RUN_ID=""
TARGET="kind"
PROMETHEUS_URL="http://localhost:9090"
LOCUST_FILE="locust/locustfile.py"
RUN_TIME="18m"
DURATION_MINUTES=18
FIXED_REPLICAS=3
HPA_MAX_REPLICAS=10
FIXED_HOST=""
HPA_HOST=""

usage() {
  cat <<'EOF'
Usage: bash scripts/run_benchmark.sh [--env-file .env] [--smoke] [--repetitions N]

Runs fixed then HPA arms with cold-start enforcement and anchored metric collection.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --smoke) SMOKE=true; shift ;;
    --repetitions) REPETITIONS="$2"; shift 2 ;;
    --run-id) RUN_ID="$2"; shift 2 ;;
    --fixed-host) FIXED_HOST="$2"; shift 2 ;;
    --hpa-host) HPA_HOST="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

load_env_file "${ENV_FILE}"

if [[ "${SMOKE}" == "true" ]]; then
  LOCUST_FILE="locust/locustfile_smoke.py"
  RUN_TIME="4m"
  DURATION_MINUTES=4
  FIXED_REPLICAS=2
  HPA_MAX_REPLICAS=3
fi

if [[ -z "${RUN_ID}" ]]; then
  RUN_ID="run-$(date -u '+%Y%m%dT%H%M%SZ')"
fi

RUN_ROOT="${REPO_ROOT}/results/runs/${RUN_ID}"
mkdir -p "${RUN_ROOT}"
cat > "${RUN_ROOT}/manifest.json" <<EOF
{
  "run_id": "${RUN_ID}",
  "smoke": ${SMOKE},
  "repetitions": ${REPETITIONS},
  "duration_minutes": ${DURATION_MINUTES},
  "fixed_replicas": ${FIXED_REPLICAS},
  "hpa_max_replicas": ${HPA_MAX_REPLICAS}
}
EOF

cleanup_on_exit() {
  cleanup_background_jobs
}
trap cleanup_on_exit EXIT INT TERM

wait_pods_zero() {
  local selector="$1"
  local count
  while true; do
    count="$(kubectl get pods -n "${NAMESPACE}" -l "${selector}" --no-headers 2>/dev/null | wc -l | tr -d ' ')"
    if [[ "${count}" == "0" ]]; then
      echo "PODS_AT_ZERO_CONFIRMED selector=${selector}"
      return 0
    fi
    sleep 2
  done
}

cold_start_arm() {
  local deployment="$1"
  local selector="$2"
  local desired="$3"

  kubectl scale deployment "${deployment}" --replicas=0 -n "${NAMESPACE}"
  wait_pods_zero "${selector}"
  kubectl scale deployment "${deployment}" --replicas="${desired}" -n "${NAMESPACE}"
  kubectl rollout status "deployment/${deployment}" -n "${NAMESPACE}" --timeout=180s

  local ready
  ready="$(kubectl get deployment "${deployment}" -n "${NAMESPACE}" -o jsonpath='{.status.readyReplicas}')"
  if [[ "${ready}" != "${desired}" ]]; then
    die "READY_REPLICAS_MATCH_DECLARED failed deployment=${deployment} expected=${desired} ready=${ready:-0}"
  fi
  echo "READY_REPLICAS_MATCH_DECLARED deployment=${deployment} ready=${ready}"
}

run_locust_arm() {
  local arm="$1"
  local host="$2"
  local csv_base="$3"
  local log_file="$4"
  local rep_dir="$5"

  local t0
  t0="$(iso_now)"
  local t0_file="${rep_dir}/t0_${arm}.txt"
  echo "LOAD_START t0=${t0}" | tee -a "${rep_dir}/run.log"
  echo "${t0}" > "${t0_file}"

  local hb_pid
  hb_pid="$(heartbeat_start "${rep_dir}/run.log" "locust-${arm}")"
  register_heartbeat_pid "${hb_pid}"

  # LoadTestShape controls users/spawn-rate; do not pass --users or --spawn-rate.
  locust_cmd -f "${REPO_ROOT}/${LOCUST_FILE}" \
    --host "${host}" \
    --headless \
    --run-time "${RUN_TIME}" \
    --csv "${csv_base}" \
    --csv-full-history \
    --logfile "${log_file}"

  heartbeat_stop "${hb_pid}"
  cat "${t0_file}"
}

collect_arm_metrics() {
  local mode="$1"
  local start_iso="$2"
  local end_iso="$3"
  local output="$4"
  local assert_replicas="${5:-}"
  local max_replicas="${6:-}"

  local args=(
    python3 "${REPO_ROOT}/analysis/collect_metrics.py"
    --mode "${mode}"
    --prometheus-url "${PROMETHEUS_URL}"
    --start "${start_iso}"
    --end "${end_iso}"
    --duration-minutes "${DURATION_MINUTES}"
    --namespace "${NAMESPACE}"
    --run-id "${RUN_ID}"
    --cluster-name "${CLUSTER_NAME:-kind-smoke}"
    --output "${output}"
  )
  if [[ -n "${assert_replicas}" ]]; then
    args+=(--assert-replicas "${assert_replicas}")
  fi
  if [[ -n "${max_replicas}" ]]; then
    args+=(--max-replicas "${max_replicas}")
  fi
  "${args[@]}"
}

run_one_repetition() {
  local rep="$1"
  local rep_dir="${RUN_ROOT}/rep-${rep}"
  mkdir -p "${rep_dir}"

  local status="PASS"
  local reason="ok"

  {
    echo "RUN_ID=${RUN_ID} REP=${rep} SMOKE=${SMOKE}"

    kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" >/dev/null 2>&1 &
    local pf_pid=$!
    register_port_forward_pid "${pf_pid}"
    sleep 3

    cold_start_arm "hpa-eval-fixed" "app=hpa-eval,experiment=fixed" "${FIXED_REPLICAS}"
    local t0_fixed
    t0_fixed="$(run_locust_arm fixed "${FIXED_HOST}" "${rep_dir}/locust_fixed" "${rep_dir}/run.log" "${rep_dir}")"
    local t1_fixed
    t1_fixed="$(iso_now)"
    collect_arm_metrics fixed "${t0_fixed}" "${t1_fixed}" "${rep_dir}/fixed_metrics.csv" "${FIXED_REPLICAS}"

    cold_start_arm "hpa-eval-hpa" "app=hpa-eval,experiment=hpa" 1
    local t0_hpa
    t0_hpa="$(run_locust_arm hpa "${HPA_HOST}" "${rep_dir}/locust_hpa" "${rep_dir}/run.log" "${rep_dir}")"
    local t1_hpa
    t1_hpa="$(iso_now)"
    collect_arm_metrics hpa "${t0_hpa}" "${t1_hpa}" "${rep_dir}/hpa_metrics.csv" 1 "${HPA_MAX_REPLICAS}"

    python3 "${REPO_ROOT}/analysis/ingest_locust.py" \
      --fixed-stats "${rep_dir}/locust_fixed_stats.csv" \
      --hpa-stats "${rep_dir}/locust_hpa_stats.csv" \
      --output "${rep_dir}/locust_summary.json"

    python3 "${REPO_ROOT}/analysis/analyze_results.py" \
      --fixed "${rep_dir}/fixed_metrics.csv" \
      --hpa "${rep_dir}/hpa_metrics.csv" \
      --output-dir "${rep_dir}/figures" || true
  } > "${rep_dir}/rep.log" 2>&1 || {
    status="FAIL"
    reason="$(tail -n 1 "${rep_dir}/rep.log" 2>/dev/null || echo unknown)"
  }

  printf '{"status":"%s","reason":"%s"}\n' "${status}" "${reason}" > "${rep_dir}/status.json"
  echo "${status}"
}

discover_hosts_kind() {
  FIXED_HOST="http://127.0.0.1:30080"
  HPA_HOST="http://127.0.0.1:30081"
}

main() {
  if [[ -z "${FIXED_HOST}" || -z "${HPA_HOST}" ]]; then
    discover_hosts_kind
  fi

  local attempted=0 passed=0
  local rep
  for ((rep=1; rep<=REPETITIONS; rep++)); do
  attempted=$((attempted + 1))
    if [[ "$(run_one_repetition "${rep}")" == "PASS" ]]; then
      passed=$((passed + 1))
    fi
  done

  local final_state="COMPLETE"
  local final_reason="all repetitions passed"
  if [[ "${passed}" -eq 0 ]]; then
    final_state="FAILED"
    final_reason="0/${attempted} repetitions passed"
  elif [[ "${passed}" -lt "${attempted}" ]]; then
    final_state="PARTIAL"
    final_reason="${passed}/${attempted} repetitions passed"
  fi

  write_status_file "${RUN_ROOT}" "${final_state}" "${final_reason}"
  echo "SUMMARY attempted=${attempted} passed=${passed}"
}

main "$@"
