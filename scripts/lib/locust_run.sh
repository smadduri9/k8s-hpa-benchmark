#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, repo .venv locust.
# Bounded Locust invocation: reachability preflight, explicit --run-time, wall-clock kill.

set -euo pipefail

if [[ -z "${_LOCUST_RUN_LIB_LOADED:-}" ]]; then
  _LOCUST_RUN_LIB_LOADED=1
  LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # shellcheck source=common.sh
  source "${LIB_DIR}/common.sh"
  # shellcheck source=cleanup.sh
  source "${LIB_DIR}/cleanup.sh"
fi

LOCUST_WALL_MARGIN_SEC="${LOCUST_WALL_MARGIN_SEC:-60}"
LOCUST_CONNECT_TIMEOUT_SEC="${LOCUST_CONNECT_TIMEOUT_SEC:-5}"

run_time_to_seconds() {
  local run_time="$1"
  case "${run_time}" in
    *m)
      echo $((${run_time%m} * 60))
      ;;
    *s)
      echo "${run_time%s}"
      ;;
    *)
      die "unsupported locust --run-time format: ${run_time}"
      ;;
  esac
}

harness_echo() {
  local harness_log="${1:-}"
  shift
  if [[ -n "${harness_log}" ]]; then
    echo "$@" >> "${harness_log}"
  fi
  echo "$@" >&2
}

verify_load_target_reachable() {
  local host="$1"
  local harness_log="${2:-}"
  local health_url="${host%/}/health"
  if ! curl -sf --max-time "${LOCUST_CONNECT_TIMEOUT_SEC}" "${health_url}" >/dev/null; then
    die "LOAD_TARGET_UNREACHABLE url=${health_url} timeout_sec=${LOCUST_CONNECT_TIMEOUT_SEC}"
  fi
  harness_echo "${harness_log}" "LOAD_TARGET_REACHABLE url=${health_url}"
}

print_locust_command_line() {
  local harness_log="${1:-}"
  shift
  local locust_file="$1"
  local host="$2"
  local run_time="$3"
  local csv_base="$4"
  local log_file="$5"
  local -a cmd=(
    "${VENV_LOCUST}"
    -f "${locust_file}"
    --host "${host}"
    --headless
    --run-time "${run_time}"
    --csv "${csv_base}"
    --csv-full-history
    --logfile "${log_file}"
  )
  local cmd_line=""
  local part
  for part in "${cmd[@]}"; do
    cmd_line+="$(printf '%q ' "${part}")"
  done
  harness_echo "${harness_log}" "LOCUST_CMD=${cmd_line}"
}

run_locust_bounded() {
  local locust_file="$1"
  local host="$2"
  local run_time="$3"
  local csv_base="$4"
  local log_file="$5"
  local harness_log="${6:-}"

  require_venv
  verify_load_target_reachable "${host}" "${harness_log}"

  local run_secs wall_secs
  run_secs="$(run_time_to_seconds "${run_time}")"
  wall_secs=$((run_secs + LOCUST_WALL_MARGIN_SEC))

  print_locust_command_line "${harness_log}" \
    "${locust_file}" "${host}" "${run_time}" "${csv_base}" "${log_file}"
  harness_echo "${harness_log}" \
    "LOCUST_WALL_CLOCK_SEC=${wall_secs} run_time_sec=${run_secs} margin_sec=${LOCUST_WALL_MARGIN_SEC}"

  "${VENV_LOCUST}" \
    -f "${locust_file}" \
    --host "${host}" \
    --headless \
    --run-time "${run_time}" \
    --csv "${csv_base}" \
    --csv-full-history \
    --logfile "${log_file}" >> "${log_file}" 2>&1 &
  local locust_pid=$!
  register_locust_pid "${locust_pid}"

  (
    sleep "${wall_secs}"
    if kill -0 "${locust_pid}" 2>/dev/null; then
      echo "LOCUST_TIMEOUT run_time=${run_time} wall_sec=${wall_secs} pid=${locust_pid}" >> "${log_file}"
      kill "${locust_pid}" 2>/dev/null || true
      sleep 2
      kill -9 "${locust_pid}" 2>/dev/null || true
    fi
  ) &
  local watcher_pid=$!
  register_heartbeat_pid "${watcher_pid}"

  local rc=0
  wait "${locust_pid}" || rc=$?

  kill "${watcher_pid}" 2>/dev/null || true
  wait "${watcher_pid}" 2>/dev/null || true

  if [[ "${rc}" -ne 0 ]]; then
    if grep -q "LOCUST_TIMEOUT" "${log_file}" 2>/dev/null; then
      die "LOCUST_TIMEOUT run_time=${run_time} wall_sec=${wall_secs}"
    fi
    die "LOCUST_FAILED exit_code=${rc}"
  fi

  if [[ ! -f "${csv_base}_stats.csv" ]]; then
    die "LOCUST_STATS_MISSING csv_base=${csv_base}"
  fi

  harness_echo "${harness_log}" "LOCUST_COMPLETE run_time=${run_time} csv_base=${csv_base}"
}
