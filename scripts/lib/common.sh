#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, docker.
# Shared helpers for benchmark scripts.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NAMESPACE="${NAMESPACE:-hpa-eval}"

log() {
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"
}

die() {
  echo "ERROR: $*" >&2
  exit 1
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

locust_cmd() {
  if command -v locust >/dev/null 2>&1; then
    locust "$@"
  else
    python3 -m locust "$@"
  fi
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
