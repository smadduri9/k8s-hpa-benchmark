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
PROBE_TIMEOUT_SEC="${CAPACITY_PROBE_TIMEOUT_SEC:-720}"
PROBE_CPU_LIMIT_M="${PROBE_CPU_LIMIT_M:-1000}"
PROBE_CPU_STOP_M=$((PROBE_CPU_LIMIT_M * 80 / 100))
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
  printf '%s\n' "${lines}" | "${VENV_PYTHON}" - <<'PY'
import sys
from statistics import median

def parse_millicores(value: str):
    value = value.strip()
    if not value:
        return None
    if value.endswith("m"):
        return int(value[:-1])
    return int(float(value) * 1000)

values = []
for line in sys.stdin:
    parts = line.split()
    if len(parts) < 2:
        continue
    parsed = parse_millicores(parts[1])
    if parsed is not None:
        values.append(parsed)
if not values:
    print("MISSING")
else:
    print(int(median(values)))
PY
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
probe_log "CPU_SOURCE=metrics-server cmd=kubectl_top_pods selector=${FIXED_SELECTOR} unit=millicores stop_threshold_m=${PROBE_CPU_STOP_M}"
probe_log "CPU_STAT=median"

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

metrics_server_assert_ready "${NAMESPACE}" || die_probe "METRICS_SERVER_UNAVAILABLE"

declared="$(deployment_declared_replicas hpa-eval-fixed "${NAMESPACE}")"
current="$(kubectl get deployment hpa-eval-fixed -n "${NAMESPACE}" -o jsonpath='{.spec.replicas}' 2>/dev/null || true)"
if [[ "${current}" != "${declared}" ]]; then
  die_probe "CAPACITY_PROBE_FIXED_REPLICAS_MISMATCH spec=${current:-MISSING} declared=${declared}"
fi
probe_log "CAPACITY_PROBE_FIXED_ARM replicas=${current}"

ensure_loadbalancer_hosts_ready
fixed_host="${FIXED_HOST}"
probe_log "CAPACITY_PROBE_TARGET host=${fixed_host}"

steps_csv="${OUTPUT_DIR}/steps.csv"
printf '%s\n' "step,users,rps,median_millicores,ready_pods,stop_trigger" > "${steps_csv}"

probe_start_epoch="$(_iso_to_epoch "$(iso_now)")"
step=0
users="${PROBE_START_USERS}"
prev_rps_per_user=""
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

  probe_log "CAPACITY_PROBE_STEP_BEGIN step=${step} users=${users} elapsed_sec=${elapsed}"

  locust_start_bounded_users \
    "${REPO_ROOT}/locust/locustfile_warmup.py" \
    "${fixed_host}" \
    "${PROBE_STEP_SEC}s" \
    "${csv_base}" \
    "${log_file}" \
    "${OUTPUT_DIR}/probe.log" \
    "${users}" \
    "${users}"

  probe_locust_wait "${PROBE_STEP_SEC}s" "${csv_base}" "${log_file}"

  ready_count="$(ready_fixed_pod_count)"
  if [[ "${ready_count}" != "${declared}" ]]; then
    die_probe "CAPACITY_PROBE_REPLICA_BELOW_DECLARED ready=${ready_count} declared=${declared}"
  fi

  top_lines="$(collect_fixed_pod_cpu_lines)"
  median_m="$(median_millicores_from_top "${top_lines}")"
  rps="$(read_locust_rps "${csv_base}_stats.csv")"

  rps_per_user=""
  if [[ "${rps}" != "MISSING" && "${users}" -gt 0 ]]; then
    rps_per_user="$(awk -v r="${rps}" -v u="${users}" 'BEGIN { printf "%.6f", r / u }')"
  else
    rps_per_user="MISSING"
  fi

  stop_trigger="none"
  if [[ "${median_m}" != "MISSING" && "${median_m}" -ge "${PROBE_CPU_STOP_M}" ]]; then
    stop_trigger="cpu_saturation"
  elif [[ -n "${prev_rps_per_user}" && "${rps_per_user}" != "MISSING" && "${prev_rps_per_user}" != "MISSING" ]]; then
    if awk -v cur="${rps_per_user}" -v prev="${prev_rps_per_user}" 'BEGIN { exit !(cur < prev) }'; then
      stop_trigger="rps_per_user_drop"
    fi
  fi

  printf '%s\n' "${step},${users},${rps},${median_m},${ready_count},${stop_trigger}" >> "${steps_csv}"
  probe_log "CAPACITY_PROBE_STEP_END step=${step} users=${users} rps=${rps} median_millicores=${median_m} rps_per_user=${rps_per_user} stop_trigger=${stop_trigger}"

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
