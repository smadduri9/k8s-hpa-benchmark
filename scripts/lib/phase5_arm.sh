#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, repo venv.
# Phase 5 arm helpers: warmup users, PASS skip, HPA manifest swap, scale assertion.

set -euo pipefail

PHASE5_ARMS=(hpa_tuned hpa_stock fixed)
PHASE5_SHAPE_ORDER=(wc98_flash wc98_ramp wc98_constant wc98_periodic rr_periodic)
# Prometheus query + scrape budget per arm; overestimate is intentional for operator planning.
PHASE5_METRICS_COLLECT_BUDGET_SEC="${PHASE5_METRICS_COLLECT_BUDGET_SEC:-300}"

shape_matrix_n() {
  case "$1" in
    wc98_flash) echo 6 ;;
    wc98_ramp|wc98_constant|wc98_periodic|rr_periodic) echo 3 ;;
    *) die "unsupported phase5 shape: $1" ;;
  esac
}

phase5_valid_shape_names_csv() {
  local IFS=,
  echo "${PHASE5_SHAPE_ORDER[*]}"
}

_phase5_run_time_to_seconds() {
  case "$1" in
    *m) echo $((${1%m} * 60)) ;;
    *s) echo "${1%s}" ;;
    *) die "unsupported run_time: $1" ;;
  esac
}

phase5_arm_wall_estimate_sec() {
  local cold_start_sec="${COLD_START_READINESS_TIMEOUT_SEC:-180}"
  local preroll_sec="${METRICS_RATE_PREROLL_SEC:-60}"
  local warmup_sec
  warmup_sec="$(_phase5_run_time_to_seconds "${WARMUP_RUN_TIME:-5m}")"
  local load_sec=1080
  local locust_margin="${LOCUST_WALL_MARGIN_SEC:-60}"
  local collector_ready_sec=30
  local overhead_sec=45
  echo $((cold_start_sec + preroll_sec + warmup_sec + load_sec + locust_margin + collector_ready_sec + PHASE5_METRICS_COLLECT_BUDGET_SEC + overhead_sec))
}

phase5_format_wall_estimate() {
  local sec="$1"
  local h=$((sec / 3600))
  local m=$(((sec % 3600) / 60))
  printf '%dh%02dm' "${h}" "${m}"
}

_phase5_trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "${s}"
}

phase5_shape_is_known() {
  local name="$1"
  local known
  for known in "${PHASE5_SHAPE_ORDER[@]}"; do
    if [[ "${name}" == "${known}" ]]; then
      return 0
    fi
  done
  return 1
}

# Prints one shape per line in deliberate matrix order (subset of PHASE5_SHAPE_ORDER).
phase5_resolve_shapes() {
  local shapes_csv="${1:-}"
  local csv_remain item known

  if [[ -z "${shapes_csv}" ]]; then
    printf '%s\n' "${PHASE5_SHAPE_ORDER[@]}"
    return 0
  fi

  csv_remain="${shapes_csv},"
  while [[ -n "${csv_remain}" ]]; do
    item="${csv_remain%%,*}"
    csv_remain="${csv_remain#${item},}"
    item="$(_phase5_trim "${item}")"
    [[ -n "${item}" ]] || continue
    if ! phase5_shape_is_known "${item}"; then
      echo "ERROR: PHASE5_MATRIX_UNKNOWN_SHAPE shape=${item} valid=$(phase5_valid_shape_names_csv)" >&2
      return 1
    fi
  done

  for known in "${PHASE5_SHAPE_ORDER[@]}"; do
    csv_remain="${shapes_csv},"
    while [[ -n "${csv_remain}" ]]; do
      item="${csv_remain%%,*}"
      csv_remain="${csv_remain#${item},}"
      item="$(_phase5_trim "${item}")"
      if [[ "${item}" == "${known}" ]]; then
        printf '%s\n' "${known}"
        break
      fi
    done
  done
}

phase5_effective_reps() {
  local shape="$1"
  local max_reps="${2:-}"
  local defined
  defined="$(shape_matrix_n "${shape}")"
  if [[ -z "${max_reps}" ]]; then
    echo "${defined}"
    return 0
  fi
  if (( max_reps < defined )); then
    echo "${max_reps}"
  else
    echo "${defined}"
  fi
}

shape_locust_rel() {
  case "$1" in
    wc98_flash) echo "locust/locustfile_wc98_flash.py" ;;
    wc98_ramp) echo "locust/locustfile_wc98_ramp.py" ;;
    wc98_constant) echo "locust/locustfile_wc98_constant.py" ;;
    wc98_periodic) echo "locust/locustfile_wc98_periodic.py" ;;
    rr_periodic) echo "locust/locustfile_rr_periodic.py" ;;
    *) die "unsupported phase5 shape: $1" ;;
  esac
}

shape_min_users() {
  local locust_file="$1"
  "${VENV_PYTHON}" - "${REPO_ROOT}/${locust_file}" <<'PY'
import importlib.util
import sys
from pathlib import Path

path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("shape_mod", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.min_users())
PY
}

arm_dir() {
  local rep_dir="$1"
  local arm="$2"
  printf '%s/%s' "${rep_dir}" "${arm}"
}

arm_is_complete() {
  local dir="$1"
  local arm="$2"
  local status="${dir}/STATUS"
  [[ -f "${status}" ]] || return 1
  [[ "$(head -n 1 "${status}")" == "PASS" ]] || return 1
  [[ -f "${dir}/t0.txt" ]] || return 1
  [[ -f "${dir}/locust_${arm}_stats.csv" ]] || return 1
  [[ -f "${dir}/${arm}_metrics.csv" ]] || return 1
  return 0
}

write_arm_status() {
  local dir="$1"
  local state="$2"
  local reason="$3"
  mkdir -p "${dir}"
  printf '%s\n%s\n' "${state}" "${reason}" > "${dir}/STATUS"
}

apply_hpa_for_arm() {
  local arm="$1"
  case "${arm}" in
    hpa_tuned)
      kubectl apply -f "${REPO_ROOT}/k8s/hpa.yaml"
      ;;
    hpa_stock)
      kubectl apply -f "${REPO_ROOT}/k8s/hpa-stock.yaml"
      ;;
    fixed)
      ;;
    *)
      die "unsupported phase5 arm: ${arm}"
      ;;
  esac
}

arm_deployment() {
  case "$1" in
    fixed) echo "hpa-eval-fixed" ;;
    hpa_tuned|hpa_stock) echo "hpa-eval-hpa" ;;
    *) die "unsupported phase5 arm: $1" ;;
  esac
}

arm_selector() {
  case "$1" in
    fixed) echo "app=hpa-eval,experiment=fixed" ;;
    hpa_tuned|hpa_stock) echo "app=hpa-eval,experiment=hpa" ;;
    *) die "unsupported phase5 arm: $1" ;;
  esac
}

arm_host() {
  case "$1" in
    fixed) echo "${FIXED_HOST}" ;;
    hpa_tuned|hpa_stock) echo "${HPA_HOST}" ;;
    *) die "unsupported phase5 arm: $1" ;;
  esac
}

collect_mode() {
  case "$1" in
    fixed) echo "fixed" ;;
    hpa_tuned|hpa_stock) echo "hpa" ;;
    *) die "unsupported phase5 arm: $1" ;;
  esac
}

assert_replicas_at_floor() {
  local arm="$1"
  local deployment
  deployment="$(arm_deployment "${arm}")"
  local spec
  spec="$(kubectl get deployment "${deployment}" -n "${NAMESPACE}" -o jsonpath='{.spec.replicas}')"
  if [[ "${arm}" == "fixed" ]]; then
    local declared
    declared="$(deployment_declared_replicas "${deployment}" "${NAMESPACE}")"
    if [[ "${spec}" != "${declared}" ]]; then
      echo "ERROR: WARMUP_TRIGGERED_SCALE arm=${arm} spec_replicas=${spec} declared=${declared}" >&2
      return 1
    fi
    return 0
  fi
  local min_r
  min_r="$(hpa_min_replicas)"
  if [[ "${spec}" != "${min_r}" ]]; then
    echo "ERROR: WARMUP_TRIGGERED_SCALE arm=${arm} spec_replicas=${spec} minReplicas=${min_r}" >&2
    return 1
  fi
}

warmup_cpu_or_missing() {
  local selector="$1"
  local line
  line="$(kubectl top pods -n "${NAMESPACE}" -l "${selector}" --no-headers 2>/dev/null || true)"
  if [[ -z "${line}" ]]; then
    printf '%s' "MISSING"
    return 0
  fi
  # kubectl top output: NAME CPU(cores) MEMORY. Do not invent a percent.
  printf '%s' "${line}" | tr '\n' ';' 
}
