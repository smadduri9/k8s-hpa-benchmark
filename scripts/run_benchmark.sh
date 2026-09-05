#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, docker, kind, locust, python3.
# Idempotent benchmark runner with cold-start enforcement, smoke mode, and per-repetition status.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIBS_DIR="${SCRIPT_DIR}/lib"
# shellcheck source=lib/common.sh
source "${LIBS_DIR}/common.sh"
# shellcheck source=lib/cleanup.sh
source "${LIBS_DIR}/cleanup.sh"
# shellcheck source=lib/cold_start.sh
source "${LIBS_DIR}/cold_start.sh"
# shellcheck source=lib/locust_run.sh
source "${LIBS_DIR}/locust_run.sh"
# shellcheck source=lib/replica_sampler.sh
source "${LIBS_DIR}/replica_sampler.sh"
# shellcheck source=lib/loadbalancer.sh
source "${LIBS_DIR}/loadbalancer.sh"

ENV_FILE=""
SMOKE=false
REPETITIONS=1
RUN_ID=""
COLD_START_ONLY=false
ARM="both"
PROMETHEUS_URL="http://localhost:9090"
LOCUST_FILE="locust/locustfile.py"
RUN_TIME="18m"
DURATION_MINUTES=18
HPA_MAX_REPLICAS=10
FIXED_HOST=""
HPA_HOST=""

usage() {
  cat <<'EOF'
Usage: bash scripts/run_benchmark.sh [--env-file .env] [--smoke] [--repetitions N]
       bash scripts/run_benchmark.sh --cold-start-only --arm fixed|hpa [--smoke]

Runs fixed then HPA arms with cold-start enforcement and anchored metric collection.
Declared replica counts are read from each deployment's spec at cold-start time.
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
    --cold-start-only) COLD_START_ONLY=true; shift ;;
    --arm) ARM="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

load_env_file "${ENV_FILE}"
require_venv

if [[ "${SMOKE}" == "true" ]]; then
  LOCUST_FILE="locust/locustfile_smoke.py"
  RUN_TIME="10m"
  DURATION_MINUTES=10
  HPA_MAX_REPLICAS=3
fi

if [[ -z "${RUN_ID}" ]]; then
  RUN_ID="run-$(date -u '+%Y%m%dT%H%M%SZ')"
fi

RUN_ROOT="${REPO_ROOT}/results/runs/${RUN_ID}"
mkdir -p "${RUN_ROOT}"
MANIFEST_PATH="${RUN_ROOT}/manifest.json"
cat > "${MANIFEST_PATH}" <<EOF
{
  "run_id": "${RUN_ID}",
  "smoke": ${SMOKE},
  "repetitions": ${REPETITIONS},
  "duration_minutes": ${DURATION_MINUTES},
  "hpa_max_replicas": ${HPA_MAX_REPLICAS},
  "arms": {}
}
EOF

cleanup_on_exit() {
  cleanup_background_jobs
}
trap cleanup_on_exit EXIT INT TERM

run_locust_arm() {
  local arm="$1"
  local host="$2"
  local csv_base="$3"
  local harness_log="$4"
  local rep_dir="$5"

  local t0_file="${rep_dir}/t0_${arm}.txt"
  local locust_log="${rep_dir}/locust_${arm}.log"
  local replica_series
  local sampler_pid=""
  local deployment
  case "${arm}" in
    fixed) deployment="hpa-eval-fixed" ;;
    hpa) deployment="hpa-eval-hpa" ;;
    *) die "unsupported locust arm: ${arm}" ;;
  esac
  replica_series="$(replica_series_path "${rep_dir}" "${arm}")"

  heartbeat_start "${harness_log}" "locust-${arm}"
  local hb_pid=$!
  register_heartbeat_pid "${hb_pid}"

  locust_start_bounded \
    "${REPO_ROOT}/${LOCUST_FILE}" \
    "${host}" \
    "${RUN_TIME}" \
    "${csv_base}" \
    "${locust_log}" \
    "${harness_log}"

  local t0
  t0="$(iso_now)"
  echo "LOAD_START t0=${t0}" >> "${harness_log}"
  echo "${t0}" > "${t0_file}"
  manifest_set_load_start_t0 "${MANIFEST_PATH}" "${arm}" "${t0}"

  replica_sampler_start "${NAMESPACE}" "${deployment}" "${replica_series}" 15
  sampler_pid=$!
  register_heartbeat_pid "${sampler_pid}"

  locust_wait_bounded \
    "${REPO_ROOT}/${LOCUST_FILE}" \
    "${host}" \
    "${RUN_TIME}" \
    "${csv_base}" \
    "${locust_log}" \
    "${harness_log}"

  replica_sampler_stop "${sampler_pid}" "${replica_series}"
  echo "REPLICA_SERIES_WRITTEN path=${replica_series} arm=${arm}"

  heartbeat_stop "${hb_pid}"
}

collect_arm_metrics() {
  local mode="$1"
  local start_iso="$2"
  local end_iso="$3"
  local output="$4"
  local max_replicas="${5:-}"
  local expected_replicas="${6:-}"
  local rep_dir
  rep_dir="$(dirname "${output}")"
  local replica_series
  replica_series="$(replica_series_path "${rep_dir}" "${mode}")"

  local args=(
    "${VENV_PYTHON}" "${REPO_ROOT}/analysis/collect_metrics.py"
    --mode "${mode}"
    --prometheus-url "${PROMETHEUS_URL}"
    --start "${start_iso}"
    --end "${end_iso}"
    --duration-minutes "${DURATION_MINUTES}"
    --namespace "${NAMESPACE}"
    --run-id "${RUN_ID}"
    --cluster-name "${CLUSTER_NAME:-kind-smoke}"
    --output "${output}"
    --replica-series "${replica_series}"
  )
  if [[ "${mode}" == "fixed" ]]; then
    if [[ -z "${expected_replicas}" ]]; then
      expected_replicas="$(deployment_declared_replicas hpa-eval-fixed "${NAMESPACE}")"
    fi
    args+=(--assert-replicas "${expected_replicas}")
    echo "ASSERT_REPLICAS_SOURCE=deployment_spec deployment=hpa-eval-fixed declared=${expected_replicas}"
  fi
  if [[ "${mode}" == "hpa" ]]; then
    local hpa_min
    hpa_min="$(hpa_min_replicas)"
    args+=(--min-replicas "${hpa_min}")
    echo "HPA_MIN_REPLICAS_SOURCE=hpa_spec minReplicas=${hpa_min}"
  fi
  if [[ -n "${max_replicas}" ]]; then
    args+=(--max-replicas "${max_replicas}")
  fi
  args+=(--step 15)
  "${args[@]}"
}

run_cold_start_only() {
  case "${ARM}" in
    fixed)
      cold_start_arm "hpa-eval-fixed" "app=hpa-eval,experiment=fixed" "${NAMESPACE}" "${MANIFEST_PATH}" "fixed" "${COLD_START_READINESS_TIMEOUT_SEC}" "true"
      ;;
    hpa)
      cold_start_arm "hpa-eval-hpa" "app=hpa-eval,experiment=hpa" "${NAMESPACE}" "${MANIFEST_PATH}" "hpa" "${COLD_START_READINESS_TIMEOUT_SEC}" "true"
      ;;
    *)
      die "unsupported --arm value for --cold-start-only: ${ARM}"
      ;;
  esac
}

run_one_repetition() {
  local rep="$1"
  local rep_dir="${RUN_ROOT}/rep-${rep}"
  mkdir -p "${rep_dir}"

  if [[ "${COLD_START_ONLY}" == "true" ]]; then
    run_cold_start_only | tee "${rep_dir}/coldstart.log"
    return 0
  fi

  local status="PASS"
  local reason="ok"
  local rep_rc=0

  set +e
  {
    echo "RUN_ID=${RUN_ID} REP=${rep} SMOKE=${SMOKE}"
    echo "LOCUST_FILE=${LOCUST_FILE} RUN_TIME=${RUN_TIME} FIXED_HOST=${FIXED_HOST} HPA_HOST=${HPA_HOST}"

    kubectl port-forward svc/prometheus 9090:9090 -n "${NAMESPACE}" >/dev/null 2>&1 &
    local pf_pid=$!
    register_port_forward_pid "${pf_pid}"
    sleep 3

    local expected_fixed
    cold_start_arm "hpa-eval-fixed" "app=hpa-eval,experiment=fixed" "${NAMESPACE}" "${MANIFEST_PATH}" "fixed"
    expected_fixed="$(deployment_declared_replicas hpa-eval-fixed "${NAMESPACE}")"
    local pods_ready_fixed
    pods_ready_fixed="$(iso_now)"
    echo "${pods_ready_fixed}" > "${rep_dir}/pods_ready_fixed.txt"
    ensure_metrics_preroll "${pods_ready_fixed}"
    run_locust_arm fixed "${FIXED_HOST}" "${rep_dir}/locust_fixed" "${rep_dir}/rep.log" "${rep_dir}"
    local t0_fixed
    t0_fixed="$(<"${rep_dir}/t0_fixed.txt")"
    local t1_fixed
    t1_fixed="$(iso_add_run_time "${t0_fixed}" "${RUN_TIME}")"
    collect_arm_metrics fixed "${t0_fixed}" "${t1_fixed}" "${rep_dir}/fixed_metrics.csv" "" "${expected_fixed}"

    cold_start_arm "hpa-eval-hpa" "app=hpa-eval,experiment=hpa" "${NAMESPACE}" "${MANIFEST_PATH}" "hpa"
    local pods_ready_hpa
    pods_ready_hpa="$(iso_now)"
    echo "${pods_ready_hpa}" > "${rep_dir}/pods_ready_hpa.txt"
    ensure_metrics_preroll "${pods_ready_hpa}"
    run_locust_arm hpa "${HPA_HOST}" "${rep_dir}/locust_hpa" "${rep_dir}/rep.log" "${rep_dir}"
    local t0_hpa
    t0_hpa="$(<"${rep_dir}/t0_hpa.txt")"
    local t1_hpa
    t1_hpa="$(iso_add_run_time "${t0_hpa}" "${RUN_TIME}")"
    collect_arm_metrics hpa "${t0_hpa}" "${t1_hpa}" "${rep_dir}/hpa_metrics.csv" "${HPA_MAX_REPLICAS}"

    venv_python "${REPO_ROOT}/analysis/ingest_locust.py" \
      --fixed-stats "${rep_dir}/locust_fixed_stats.csv" \
      --hpa-stats "${rep_dir}/locust_hpa_stats.csv" \
      --output "${rep_dir}/locust_summary.json"

    venv_python "${REPO_ROOT}/analysis/analyze_results.py" \
      --fixed "${rep_dir}/fixed_metrics.csv" \
      --hpa "${rep_dir}/hpa_metrics.csv" \
      --locust-hpa-stats "${rep_dir}/locust_hpa_stats.csv" \
      --output-dir "${rep_dir}/figures"
  } >> "${rep_dir}/rep.log" 2>&1
  rep_rc=$?
  set -e
  cat "${rep_dir}/rep.log" >&2

  if [[ "${rep_rc}" -ne 0 ]]; then
    status="FAIL"
    reason="$(tail -n 1 "${rep_dir}/rep.log" 2>/dev/null || echo unknown)"
  fi

  printf '{"status":"%s","reason":"%s"}\n' "${status}" "${reason}" > "${rep_dir}/status.json"
  if [[ "${status}" == "PASS" ]]; then
    return 0
  fi
  return 1
}

main() {
  if [[ "${COLD_START_ONLY}" != "true" ]]; then
    ensure_loadbalancer_hosts_ready
  fi

  local attempted=0 passed=0
  local rep
  for ((rep=1; rep<=REPETITIONS; rep++)); do
    attempted=$((attempted + 1))
    if run_one_repetition "${rep}"; then
      passed=$((passed + 1))
    fi
  done

  if [[ "${COLD_START_ONLY}" == "true" ]]; then
    return 0
  fi

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

  if [[ "${passed}" -eq "${attempted}" && "${attempted}" -gt 0 ]]; then
    exit 0
  fi
  exit 1
}

main "$@"
