#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, locust via repo venv.
# Flash-first Phase 5 matrix with arm-level resume and optional shape/rep scope.
# Usage: bash scripts/run_phase5_matrix.sh --env-file .env [--resume] [--shapes LIST] [--max-reps N]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/phase5_arm.sh
source "${SCRIPT_DIR}/lib/phase5_arm.sh"

ENV_FILE=""
RESUME=false
SHAPES_CSV=""
MAX_REPS=""
MAX_REPS_SET=false

usage() {
  cat <<'EOF'
Usage: bash scripts/run_phase5_matrix.sh --env-file .env [--resume] [--shapes LIST] [--max-reps N]

Order is deliberate: wc98_flash n=6 first (only significable cell), then n=3 shapes.
--shapes limits to a comma-separated subset (order follows the matrix definition).
--max-reps caps repetitions per shape (dress rehearsal: --shapes wc98_flash --max-reps 1).
Resume unit is arm: skips rep-N/<arm>/STATUS PASS with required CSVs.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --resume) RESUME=true; shift ;;
    --shapes) SHAPES_CSV="$2"; shift 2 ;;
    --max-reps)
      MAX_REPS="$2"
      MAX_REPS_SET=true
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

if [[ "${MAX_REPS_SET}" == "true" ]]; then
  if ! [[ "${MAX_REPS}" =~ ^[0-9]+$ ]] || [[ "${MAX_REPS}" -lt 1 ]]; then
    die "PHASE5_MATRIX_MAX_REPS_INVALID value=${MAX_REPS}"
  fi
fi

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

SELECTED_SHAPES=()
shape_list_file="$(mktemp "${TMPDIR:-/tmp}/phase5-shapes.XXXXXX")"
if ! phase5_resolve_shapes "${SHAPES_CSV}" > "${shape_list_file}"; then
  rm -f "${shape_list_file}"
  exit 1
fi
while IFS= read -r shape; do
  [[ -n "${shape}" ]] || continue
  SELECTED_SHAPES+=("${shape}")
done < "${shape_list_file}"
rm -f "${shape_list_file}"

if [[ ${#SELECTED_SHAPES[@]} -eq 0 ]]; then
  die "PHASE5_MATRIX_SHAPES_EMPTY"
fi

arm_wall_sec="$(phase5_arm_wall_estimate_sec)"
arms_per_rep="${#PHASE5_ARMS[@]}"
total_arms=0
total_wall_sec=0
shape_plan_csv=""

for shape in "${SELECTED_SHAPES[@]}"; do
  matrix_n="$(shape_matrix_n "${shape}")"
  if [[ "${MAX_REPS_SET}" == "true" ]]; then
    effective_n="$(phase5_effective_reps "${shape}" "${MAX_REPS}")"
  else
    effective_n="${matrix_n}"
  fi
  shape_arms=$((effective_n * arms_per_rep))
  total_arms=$((total_arms + shape_arms))
  total_wall_sec=$((total_wall_sec + shape_arms * arm_wall_sec))
  capped="false"
  if (( effective_n < matrix_n )); then
    capped="true"
  fi
  echo "PHASE5_MATRIX_PLAN_SHAPE shape=${shape} matrix_reps_defined=${matrix_n} effective_reps=${effective_n} capped=${capped} arms=${shape_arms}"
  if [[ -n "${shape_plan_csv}" ]]; then
    shape_plan_csv+=","
  fi
  shape_plan_csv+="${shape}@${effective_n}/${matrix_n}"
done

wall_human="$(phase5_format_wall_estimate "${total_wall_sec}")"
shapes_human="$(IFS=,; echo "${SELECTED_SHAPES[*]}")"
max_reps_log="all"
if [[ "${MAX_REPS_SET}" == "true" ]]; then
  max_reps_log="${MAX_REPS}"
fi

echo "PHASE5_MATRIX_START SHAPE_MEAN_USERS=${SHAPE_MEAN_USERS} resume=${RESUME} shapes=${shapes_human} max_reps=${max_reps_log}"
echo "PHASE5_MATRIX_PLAN shapes=${shapes_human} shape_count=${#SELECTED_SHAPES[@]} arms_per_rep=${arms_per_rep} arms_total=${total_arms} wall_estimate_sec=${total_wall_sec} wall_estimate=${wall_human} arm_wall_budget_sec=${arm_wall_sec} shape_reps=${shape_plan_csv}"
echo "PHASE5_MATRIX_PLAN_NOTE wall_estimate uses cold_start_timeout+warmup+18m_load+metrics_budget; actual may be shorter"

for shape in "${SELECTED_SHAPES[@]}"; do
  matrix_n="$(shape_matrix_n "${shape}")"
  if [[ "${MAX_REPS_SET}" == "true" ]]; then
    effective_n="$(phase5_effective_reps "${shape}" "${MAX_REPS}")"
  else
    effective_n="${matrix_n}"
  fi
  run_id="run-phase5-${shape}"
  echo "PHASE5_SHAPE_START shape=${shape} matrix_reps_defined=${matrix_n} effective_reps=${effective_n} run_id=${run_id}"
  local_rep
  for ((local_rep=1; local_rep<=effective_n; local_rep++)); do
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
        --repetitions "${effective_n}" \
        --matrix-repetitions-defined "${matrix_n}" \
        --run-id "${run_id}"
    done
  done
  echo "PHASE5_SHAPE_DONE shape=${shape}"
done

echo "PHASE5_MATRIX_COMPLETE"
