#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl.
# Sample deployment spec/status/ready replica counts on an interval during load.

set -euo pipefail

if [[ -z "${_REPLICA_SAMPLER_LIB_LOADED:-}" ]]; then
  _REPLICA_SAMPLER_LIB_LOADED=1
  LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # shellcheck source=common.sh
  source "${LIB_DIR}/common.sh"
fi

REPLICA_SAMPLE_INTERVAL_SEC="${REPLICA_SAMPLE_INTERVAL_SEC:-15}"
REPLICA_SERIES_HEADER="timestamp,spec_replicas,status_replicas,ready_replicas"

replica_series_path() {
  local rep_dir="$1"
  local arm="$2"
  echo "${rep_dir}/replica_series_${arm}.csv"
}

# One kubectl call: spec.replicas, status.replicas, status.readyReplicas.
# .status.readyReplicas is omitted when zero — record empty as 0, not MISSING.
_read_replica_fields() {
  local namespace="$1"
  local deployment="$2"
  local raw spec_count status_count ready_count
  raw="$(kubectl get deployment "${deployment}" -n "${namespace}" \
    -o jsonpath='{.spec.replicas},{.status.replicas},{.status.readyReplicas}' 2>/dev/null || true)"
  IFS=',' read -r spec_count status_count ready_count <<< "${raw}"
  if [[ -z "${spec_count}" ]]; then
    spec_count=0
  fi
  if [[ -z "${status_count}" ]]; then
    status_count=0
  fi
  if [[ -z "${ready_count}" ]]; then
    ready_count=0
  fi
  printf '%s,%s,%s' "${spec_count}" "${status_count}" "${ready_count}"
}

replica_sampler_start() {
  local namespace="$1"
  local deployment="$2"
  local output="$3"
  local interval="${4:-${REPLICA_SAMPLE_INTERVAL_SEC}}"

  printf '%s\n' "${REPLICA_SERIES_HEADER}" > "${output}"
  rm -f "${output}.stop"

  {
    while true; do
      if [[ -f "${output}.stop" ]]; then
        break
      fi
      local ts fields
      ts="$(iso_now)"
      fields="$(_read_replica_fields "${namespace}" "${deployment}")"
      printf '%s,%s\n' "${ts}" "${fields}" >> "${output}"
      sleep "${interval}"
    done
  } &
}

replica_sampler_stop() {
  local pid="${1:-}"
  local output="${2:-}"
  if [[ -n "${output}" ]]; then
    touch "${output}.stop"
  fi
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}" 2>/dev/null || true
    wait "${pid}" 2>/dev/null || true
  fi
  rm -f "${output}.stop"
}

# For smoke tests that call collect_metrics without a full Locust arm: sample once and
# expand constant counts across [start_iso, end_iso] at step_sec intervals.
replica_series_write_constant() {
  local namespace="$1"
  local deployment="$2"
  local start_iso="$3"
  local end_iso="$4"
  local step_sec="$5"
  local output="$6"

  local fields start_epoch end_epoch ts_epoch
  fields="$(_read_replica_fields "${namespace}" "${deployment}")"
  start_epoch="$(_iso_to_epoch "${start_iso}")"
  end_epoch="$(_iso_to_epoch "${end_iso}")"

  printf '%s\n' "${REPLICA_SERIES_HEADER}" > "${output}"
  ts_epoch="${start_epoch}"
  while (( ts_epoch <= end_epoch )); do
    local ts_iso
    ts_iso="$("${VENV_PYTHON}" -c "from datetime import datetime, timezone; print(datetime.fromtimestamp(${ts_epoch}, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))")"
    printf '%s,%s\n' "${ts_iso}" "${fields}" >> "${output}"
    ts_epoch=$((ts_epoch + step_sec))
  done
}
