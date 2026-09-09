#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, locust via repo venv.
# Flash-first Phase 5 matrix with arm-level resume.
# Usage: bash scripts/run_phase5_matrix.sh --env-file .env [--resume]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/phase5_arm.sh
source "${SCRIPT_DIR}/lib/phase5_arm.sh"

ENV_FILE=""
RESUME=false

usage() {
  cat <<'EOF'
Usage: bash scripts/run_phase5_matrix.sh --env-file .env [--resume]

Order is deliberate: wc98_flash n=6 first (only significable cell), then n=3 shapes.
Resume unit is arm: skips rep-N/<arm>/STATUS PASS with required CSVs.
EOF
}

shape_n() {
  case "$1" in
    wc98_flash) echo 6 ;;
    *) echo 3 ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --resume) RESUME=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

load_env_file "${ENV_FILE}"
require_venv

CAPACITY_PROBE_DERIVATION="${REPO_ROOT}/results/capacity_probe/derivation.json"
if [[ -f "${CAPACITY_PROBE_DERIVATION}" ]]; then
  SHAPE_MEAN_USERS="$("${VENV_PYTHON}" - "${CAPACITY_PROBE_DERIVATION}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
value = data.get("SHAPE_MEAN_USERS")
if value is None:
    raise SystemExit("CAPACITY_PROBE_DERIVATION_INVALID reason=missing_SHAPE_MEAN_USERS")
print(int(value))
PY
)"
  export SHAPE_MEAN_USERS
  echo "SHAPE_MEAN_USERS_SOURCE=capacity_probe derivation=${CAPACITY_PROBE_DERIVATION} value=${SHAPE_MEAN_USERS}"
else
  die "SHAPE_MEAN_USERS_UNCALIBRATED reason=missing_derivation_json path=${CAPACITY_PROBE_DERIVATION} run=bash scripts/run_capacity_probe.sh --env-file .env"
fi

if [[ -z "${SHAPE_MEAN_USERS}" ]]; then
  die "SHAPE_MEAN_USERS is empty"
fi

echo "PHASE5_MATRIX_START SHAPE_MEAN_USERS=${SHAPE_MEAN_USERS} resume=${RESUME}"
echo "PHASE5_MATRIX_ORDER ${PHASE5_SHAPE_ORDER[*]} (flash first: n=6 can reach p<0.05)"

for shape in "${PHASE5_SHAPE_ORDER[@]}"; do
  local_n="$(shape_n "${shape}")"
  run_id="run-phase5-${shape}"
  echo "PHASE5_SHAPE_START shape=${shape} n=${local_n} run_id=${run_id}"
  local_rep
  for ((local_rep=1; local_rep<=local_n; local_rep++)); do
    for arm in "${PHASE5_ARMS[@]}"; do
      dir="${REPO_ROOT}/results/runs/${run_id}/rep-${local_rep}/${arm}"
      if [[ "${RESUME}" == "true" ]] && arm_is_complete "${dir}" "${arm}"; then
        echo "PHASE5_ARM_SKIP shape=${shape} rep=${local_rep} arm=${arm}"
        continue
      fi
      if [[ -d "${dir}" ]] && ! arm_is_complete "${dir}" "${arm}"; then
        echo "PHASE5_ARM_RETRY shape=${shape} rep=${local_rep} arm=${arm} removing incomplete"
        rm -rf "${dir}"
      fi
      bash "${SCRIPT_DIR}/run_benchmark.sh" \
        --env-file "${ENV_FILE}" \
        --shape "${shape}" \
        --phase5 \
        --only-arm "${arm}" \
        --only-rep "${local_rep}" \
        --repetitions "${local_n}" \
        --run-id "${run_id}"
    done
  done
  echo "PHASE5_SHAPE_DONE shape=${shape}"
done

echo "PHASE5_MATRIX_COMPLETE"
