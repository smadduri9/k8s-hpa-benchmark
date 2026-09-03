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

hpa_min_replicas() {
  kubectl get hpa hpa-eval-hpa -n "${NAMESPACE}" -o jsonpath='{.spec.minReplicas}'
}

heartbeat_start() {
  local log_file="$1"
  local label="$2"
  (
    while true; do
      echo "[$(iso_now)] HEARTBEAT ${label}" >> "${log_file}"
      sleep 60
    done
  ) &
  echo $!
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
