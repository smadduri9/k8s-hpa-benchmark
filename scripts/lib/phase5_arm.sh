#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, repo venv.
# Phase 5 arm helpers: warmup users, PASS skip, HPA manifest swap, scale assertion.

set -euo pipefail

PHASE5_ARMS=(hpa_tuned hpa_stock fixed)
PHASE5_SHAPE_ORDER=(wc98_flash wc98_ramp wc98_constant wc98_periodic rr_periodic)

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
