#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, docker.
# Shared helpers for benchmark scripts.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NAMESPACE="${NAMESPACE:-hpa-eval}"
VENV_DIR="${REPO_ROOT}/.venv"
VENV_PYTHON="${VENV_DIR}/bin/python"
VENV_LOCUST="${VENV_DIR}/bin/locust"

log() {
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

require_venv() {
  if [[ ! -x "${VENV_PYTHON}" ]]; then
    die "missing venv interpreter at ${VENV_PYTHON}; create with: python3 -m venv \"${VENV_DIR}\" && \"${VENV_DIR}/bin/python\" -m pip install -r \"${REPO_ROOT}/requirements-tooling.txt\""
  fi
  if [[ ! -x "${VENV_LOCUST}" ]]; then
    die "missing venv locust at ${VENV_LOCUST}; run: \"${VENV_PYTHON}\" -m pip install -r \"${REPO_ROOT}/requirements-tooling.txt\""
  fi
}

venv_python() {
  require_venv
  "${VENV_PYTHON}" "$@"
}

locust_cmd() {
  require_venv
  "${VENV_LOCUST}" "$@"
}

load_env_file() {
  local env_file="${1:-}"
  if [[ -z "${env_file}" ]]; then
    return 0
  fi
  if [[ ! -f "${env_file}" ]]; then
    die "env file not found: ${env_file}"
  fi
  # shellcheck disable=SC1090
  set -a
  source "${env_file}"
  set +a
}

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    die "required environment variable ${name} is not set (use .env or flags)"
  fi
}

iso_now() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

# Add Locust-style duration (e.g. 4m, 60s) to an ISO8601 UTC timestamp.
iso_add_run_time() {
  local iso="$1"
  local run_time="$2"
  local num="${run_time%[mMsS]}"
  local unit="${run_time: -1}"
  local secs=0
  case "${unit}" in
    m|M) secs=$((num * 60)) ;;
    s|S) secs="${num}" ;;
    *) die "unsupported run_time duration: ${run_time}" ;;
  esac
  local result=""
  if result="$(date -u -v "+${secs}S" -jf '%Y-%m-%dT%H:%M:%SZ' "${iso}" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)"; then
    echo "${result}"
    return 0
  fi
  date -u -d "${iso} + ${secs} seconds" '+%Y-%m-%dT%H:%M:%SZ'
}

# Prometheus rate(...[1m]) pre-roll duration for benchmark scrape before LOAD_START t0.
METRICS_RATE_PREROLL_SEC="${METRICS_RATE_PREROLL_SEC:-60}"
METRICS_SAMPLE_WINDOW_SEC="${METRICS_SAMPLE_WINDOW_SEC:-600}"
METRICS_SCRAPE_SETTLE_SEC="${METRICS_SCRAPE_SETTLE_SEC:-15}"
# Match Prometheus scrape_interval in k8s/prometheus/configmap.yaml (15s).
SMOKE_METRICS_STEP="${SMOKE_METRICS_STEP:-15}"

_iso_to_epoch() {
  local iso="$1"
  local result=""
  if result="$(date -u -jf '%Y-%m-%dT%H:%M:%SZ' "${iso}" '+%s' 2>/dev/null)"; then
    echo "${result}"
    return 0
  fi
  date -u -d "${iso}" '+%s'
}

metrics_query_end_iso() {
  local result=""
  if result="$(date -u -v-"${METRICS_SCRAPE_SETTLE_SEC}"S '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)"; then
    echo "${result}"
    return 0
  fi
  date -u -d "${METRICS_SCRAPE_SETTLE_SEC} seconds ago" '+%Y-%m-%dT%H:%M:%SZ'
}

metrics_query_start_iso() {
  local window_sec="${1:-${METRICS_SAMPLE_WINDOW_SEC}}"
  local offset_sec=$((window_sec + METRICS_RATE_PREROLL_SEC + METRICS_SCRAPE_SETTLE_SEC))
  local start_iso=""
  if start_iso="$(date -u -v-"${offset_sec}"S '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)"; then
    :
  else
    start_iso="$(date -u -d "${offset_sec} seconds ago" '+%Y-%m-%dT%H:%M:%SZ')"
  fi
  echo "${start_iso}"
}

ensure_metrics_preroll() {
  local pods_ready_iso="$1"
  local preroll_sec="${METRICS_RATE_PREROLL_SEC}"
  local ready_epoch now_epoch target_epoch
  ready_epoch="$(_iso_to_epoch "${pods_ready_iso}")"
  target_epoch=$((ready_epoch + preroll_sec))
  now_epoch="$(_iso_to_epoch "$(iso_now)")"
  if (( now_epoch < target_epoch )); then
    local wait_sec=$((target_epoch - now_epoch))
    echo "METRIC_SCRAPE_PREROLL_WAIT sec=${wait_sec} ready_at=${pods_ready_iso}"
    sleep "${wait_sec}"
  else
    echo "METRIC_SCRAPE_PREROLL_SATISFIED ready_at=${pods_ready_iso} elapsed_sec=$((now_epoch - ready_epoch))"
  fi
}

hpa_min_replicas() {
  kubectl get hpa hpa-eval-hpa -n "${NAMESPACE}" -o jsonpath='{.spec.minReplicas}'
}

heartbeat_start() {
  local log_file="$1"
  local label="$2"
  { while true; do
      local line="[$(iso_now)] HEARTBEAT ${label}"
      if [[ -n "${log_file}" ]]; then
        echo "${line}" >> "${log_file}"
      else
        echo "${line}" >&2
      fi
      sleep 60
    done
  } &
}

heartbeat_stop() {
  local pid="${1:-}"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}" 2>/dev/null || true
    wait "${pid}" 2>/dev/null || true
  fi
}

write_status_file() {
  local run_dir="$1"
  local state="$2"
  local reason="$3"
  case "${state}" in
    COMPLETE|PARTIAL|FAILED) ;;
    *) die "invalid STATUS state: ${state}" ;;
  esac
  printf '%s\n%s\n' "${state}" "${reason}" > "${run_dir}/STATUS"
}
