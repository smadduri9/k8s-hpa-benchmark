#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, repo .venv locust.
# P1 capacity probe: derive SHAPE_MEAN_USERS from fixed-arm Locust ramp (not a benchmark).
# Usage: bash scripts/run_capacity_probe.sh --env-file .env [--dry-run]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/locust_run.sh
source "${SCRIPT_DIR}/lib/locust_run.sh"
# shellcheck source=lib/loadbalancer.sh
source "${SCRIPT_DIR}/lib/loadbalancer.sh"
# shellcheck source=lib/metrics_server_check.sh
source "${SCRIPT_DIR}/lib/metrics_server_check.sh"
# shellcheck source=lib/cold_start.sh
source "${SCRIPT_DIR}/lib/cold_start.sh"

ENV_FILE=""
DRY_RUN=false
PROBE_START_USERS="${PROBE_START_USERS:-20}"
PROBE_STEP_USERS="${PROBE_STEP_USERS:-10}"
PROBE_STEP_SEC="${PROBE_STEP_SEC:-45}"
PROBE_CPU_SAMPLE_LEAD_SEC="${PROBE_CPU_SAMPLE_LEAD_SEC:-5}"
PROBE_TIMEOUT_SEC="${CAPACITY_PROBE_TIMEOUT_SEC:-720}"
PROBE_CPU_LIMIT_M="${PROBE_CPU_LIMIT_M:-1000}"
PROBE_CPU_STOP_M=$((PROBE_CPU_LIMIT_M * 80 / 100))
# RPS-per-user drop: >10% decline vs prior step (sub-10% is routine between 45s windows).
PROBE_RPS_DROP_FRACTION="${PROBE_RPS_DROP_FRACTION:-0.10}"
# Require prior observed median pod CPU before RPS drop can mean saturation (10% of limit).
PROBE_CPU_OBSERVED_FLOOR_M="${PROBE_CPU_OBSERVED_FLOOR_M:-100}"
# Need at least three steps before RPS drop can stop the probe (two points cannot show a trend).
PROBE_RPS_DROP_MIN_STEPS="${PROBE_RPS_DROP_MIN_STEPS:-3}"
FIXED_SELECTOR="app=hpa-eval,experiment=fixed"
OUTPUT_DIR="${REPO_ROOT}/results/capacity_probe"

usage() {
  cat <<'EOF'
Usage: bash scripts/run_capacity_probe.sh --env-file .env [--dry-run]

Fixed arm only. Derives SHAPE_MEAN_USERS into results/capacity_probe/derivation.json.
Wall-clock cap: CAPACITY_PROBE_TIMEOUT_SEC (default 720 = 12 minutes).
EOF
}

die_probe() {
  echo "ERROR: $*" >&2
  exit 1
}

probe_log() {
  printf '%s\n' "$*" | tee -a "${OUTPUT_DIR}/probe.log"
}

ready_fixed_pod_count() {
  kubectl get pods -n "${NAMESPACE}" -l "${FIXED_SELECTOR}" --no-headers 2>/dev/null \
    | awk '$2=="1/1" { count++ } END { print count+0 }'
}

collect_fixed_pod_cpu_lines() {
  kubectl top pods -n "${NAMESPACE}" -l "${FIXED_SELECTOR}" --no-headers 2>/dev/null || true
}

median_millicores_from_top() {
  local lines="$1"
  printf '%s\n' "${lines}" | "${VENV_PYTHON}" "${SCRIPT_DIR}/lib/capacity_probe_derive.py" top-median
}

probe_locust_wait() {
  local run_time="$1"
  local csv_base="$2"
  local log_file="$3"
  local locust_pid="${LOCUST_STARTED_PID:-}"
  local watcher_pid="${LOCUST_WATCHER_PID:-}"
  local wall_secs="${LOCUST_WALL_CLOCK_SEC:-}"

  if [[ -z "${locust_pid}" ]]; then
    die_probe "LOCUST_WAIT_WITHOUT_START"
  fi

  local rc=0
  wait "${locust_pid}" || rc=$?

  if [[ -n "${watcher_pid}" ]]; then
    kill "${watcher_pid}" 2>/dev/null || true
    wait "${watcher_pid}" 2>/dev/null || true
  fi

  if grep -q "LOCUST_TIMEOUT" "${log_file}" 2>/dev/null; then
    die_probe "LOCUST_TIMEOUT run_time=${run_time} wall_sec=${wall_secs}"
  fi

  if [[ ! -s "${csv_base}_stats.csv" ]]; then
    die_probe "LOCUST_STATS_MISSING csv=${csv_base}_stats.csv"
  fi

  probe_log "LOCUST_STEP_COMPLETE run_time=${run_time} exit_rc=${rc} csv_base=${csv_base}"
}

read_locust_rps() {
  local stats_csv="$1"
  "${VENV_PYTHON}" - "${stats_csv}" <<'PY'
import csv
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file():
    print("MISSING")
    raise SystemExit(0)
with path.open(newline="", encoding="utf-8") as handle:
    for row in csv.DictReader(handle):
        if row.get("Name") != "Aggregated":
            continue
        raw = (row.get("Requests/s") or "").strip()
        if not raw or raw.upper() == "N/A":
            print("MISSING")
        else:
            print(raw)
        raise SystemExit(0)
print("MISSING")
PY
}

rps_drop_exceeds_tolerance() {
  local cur="$1"
  local prev="$2"
  local fraction="$3"
  awk -v cur="${cur}" -v prev="${prev}" -v frac="${fraction}" \
    'BEGIN { exit !(cur < prev * (1.0 - frac)) }'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die_probe "unknown argument: $1" ;;
  esac
done

load_env_file "${ENV_FILE}"
require_venv
NAMESPACE="${NAMESPACE:-hpa-eval}"

mkdir -p "${OUTPUT_DIR}"
: > "${OUTPUT_DIR}/probe.log"

probe_log "CAPACITY_PROBE_BEGIN dry_run=${DRY_RUN}"
probe_log "PROBE_START_USERS=${PROBE_START_USERS} PROBE_STEP_USERS=${PROBE_STEP_USERS} PROBE_STEP_SEC=${PROBE_STEP_SEC}"
probe_log "PROBE_CPU_SAMPLE_LEAD_SEC=${PROBE_CPU_SAMPLE_LEAD_SEC} (CPU sampled during load, not after)"
probe_log "PROBE_RPS_DROP_FRACTION=${PROBE_RPS_DROP_FRACTION} PROBE_RPS_DROP_MIN_STEPS=${PROBE_RPS_DROP_MIN_STEPS} PROBE_CPU_OBSERVED_FLOOR_M=${PROBE_CPU_OBSERVED_FLOOR_M}"
probe_log "CPU_SOURCE=metrics-server cmd=kubectl_top_pods selector=${FIXED_SELECTOR} unit=millicores stop_threshold_m=${PROBE_CPU_STOP_M}"
probe_log "CPU_STAT=median"
probe_log "RPS_SOURCE=locust_stats_csv column=Requests/s window=full_step_includes_spawn_transient"

if [[ "${DRY_RUN}" == "true" ]]; then
  if ! command -v kubectl >/dev/null 2>&1; then
    die_probe "CLUSTER_NOT_READY reason=kubectl_missing"
  fi
  if ! kubectl cluster-info >/dev/null 2>&1; then
    die_probe "CLUSTER_NOT_READY reason=kubectl_cluster_unreachable"
  fi
  probe_log "CAPACITY_PROBE_DRY_RUN_PASS"
  exit 0
fi

if ! command -v kubectl >/dev/null 2>&1; then
  die_probe "CLUSTER_NOT_READY reason=kubectl_missing"
fi

declared="$(deployment_declared_replicas hpa-eval-fixed "${NAMESPACE}")"
metrics_server_assert_top_pods "${NAMESPACE}" "${FIXED_SELECTOR}" "${declared}" \
  || die_probe "METRICS_SERVER_UNAVAILABLE"

current="$(kubectl get deployment hpa-eval-fixed -n "${NAMESPACE}" -o jsonpath='{.spec.replicas}' 2>/dev/null || true)"
if [[ "${current}" != "${declared}" ]]; then
  die_probe "CAPACITY_PROBE_FIXED_REPLICAS_MISMATCH spec=${current:-MISSING} declared=${declared}"
fi
probe_log "CAPACITY_PROBE_FIXED_ARM replicas=${current}"

ensure_loadbalancer_hosts_ready
fixed_host="${FIXED_HOST}"
probe_log "CAPACITY_PROBE_TARGET host=${fixed_host}"

cpu_sample_wait_sec=$((PROBE_STEP_SEC - PROBE_CPU_SAMPLE_LEAD_SEC))
if (( cpu_sample_wait_sec < 1 )); then
  die_probe "CAPACITY_PROBE_STEP_TOO_SHORT step_sec=${PROBE_STEP_SEC} sample_lead_sec=${PROBE_CPU_SAMPLE_LEAD_SEC}"
fi
probe_log "CAPACITY_PROBE_MEASUREMENT spawn_rate_equals_users spawn_transient_sec~1 rps_window_sec=${PROBE_STEP_SEC} cpu_sample_at_sec=${cpu_sample_wait_sec}"

steps_csv="${OUTPUT_DIR}/steps.csv"
printf '%s\n' "step,users,rps,median_millicores,ready_pods,stop_trigger" > "${steps_csv}"

probe_start_epoch="$(_iso_to_epoch "$(iso_now)")"
step=0
users="${PROBE_START_USERS}"
prev_rps_per_user=""
peak_median_m=0
stop_trigger="timeout"

while true; do
  now_epoch="$(_iso_to_epoch "$(iso_now)")"
  elapsed=$((now_epoch - probe_start_epoch))
  if (( elapsed >= PROBE_TIMEOUT_SEC )); then
    probe_log "CAPACITY_PROBE_TIMEOUT elapsed_sec=${elapsed} limit_sec=${PROBE_TIMEOUT_SEC}"
    stop_trigger="timeout"
    break
  fi

  step=$((step + 1))
  step_dir="${OUTPUT_DIR}/step-${step}"
  mkdir -p "${step_dir}"
  csv_base="${step_dir}/locust"
  log_file="${step_dir}/locust.log"
  spawn_rate="${users}"

  probe_log "CAPACITY_PROBE_STEP_BEGIN step=${step} users=${users} spawn_rate=${spawn_rate} spawn_transient_sec~1 elapsed_sec=${elapsed}"

  locust_start_bounded_users \
    "${REPO_ROOT}/locust/locustfile_warmup.py" \
    "${fixed_host}" \
    "${PROBE_STEP_SEC}s" \
    "${csv_base}" \
    "${log_file}" \
    "${OUTPUT_DIR}/probe.log" \
    "${users}" \
    "${spawn_rate}"

  sleep "${cpu_sample_wait_sec}"

  ready_count="$(ready_fixed_pod_count)"
  if [[ "${ready_count}" != "${declared}" ]]; then
    die_probe "CAPACITY_PROBE_REPLICA_BELOW_DECLARED ready=${ready_count} declared=${declared}"
  fi

  top_lines="$(collect_fixed_pod_cpu_lines)"
  median_m="$(median_millicores_from_top "${top_lines}")"
  probe_log "CAPACITY_PROBE_CPU_SAMPLE step=${step} elapsed_in_step_sec=${cpu_sample_wait_sec} median_millicores=${median_m}"

  probe_locust_wait "${PROBE_STEP_SEC}s" "${csv_base}" "${log_file}"

  rps="$(read_locust_rps "${csv_base}_stats.csv")"

  rps_per_user=""
  if [[ "${rps}" != "MISSING" && "${users}" -gt 0 ]]; then
    rps_per_user="$(awk -v r="${rps}" -v u="${users}" 'BEGIN { printf "%.6f", r / u }')"
  else
    rps_per_user="MISSING"
  fi

  if [[ "${median_m}" != "MISSING" && "${median_m}" -gt "${peak_median_m}" ]]; then
    peak_median_m="${median_m}"
  fi

  stop_trigger="none"
  if [[ "${median_m}" != "MISSING" && "${median_m}" -ge "${PROBE_CPU_STOP_M}" ]]; then
    stop_trigger="cpu_saturation"
  elif (( step >= PROBE_RPS_DROP_MIN_STEPS )) \
      && [[ "${peak_median_m}" -ge "${PROBE_CPU_OBSERVED_FLOOR_M}" ]] \
      && [[ -n "${prev_rps_per_user}" && "${rps_per_user}" != "MISSING" && "${prev_rps_per_user}" != "MISSING" ]] \
      && rps_drop_exceeds_tolerance "${rps_per_user}" "${prev_rps_per_user}" "${PROBE_RPS_DROP_FRACTION}"; then
    stop_trigger="rps_per_user_drop"
  fi

  printf '%s\n' "${step},${users},${rps},${median_m},${ready_count},${stop_trigger}" >> "${steps_csv}"
  probe_log "CAPACITY_PROBE_STEP_END step=${step} users=${users} rps=${rps} median_millicores=${median_m} rps_per_user=${rps_per_user} peak_median_m=${peak_median_m} stop_trigger=${stop_trigger}"

  if [[ "${stop_trigger}" != "none" ]]; then
    break
  fi

  prev_rps_per_user="${rps_per_user}"
  users=$((users + PROBE_STEP_USERS))
done

if [[ "${stop_trigger}" == "timeout" ]]; then
  die_probe "CAPACITY_PROBE_TIMEOUT"
fi

set +e
derive_rc=0
"${VENV_PYTHON}" "${SCRIPT_DIR}/lib/capacity_probe_derive.py" \
  --steps "${steps_csv}" \
  --out "${OUTPUT_DIR}/derivation.json" >> "${OUTPUT_DIR}/probe.log" 2>&1
derive_rc=$?
set -e

if [[ "${derive_rc}" -ne 0 ]]; then
  probe_log "CAPACITY_PROBE_DERIVE_FAIL rc=${derive_rc}"
  exit "${derive_rc}"
fi

probe_log "CAPACITY_PROBE_PASS derivation=${OUTPUT_DIR}/derivation.json"
cat "${OUTPUT_DIR}/derivation.json"
