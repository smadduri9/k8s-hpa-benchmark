#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, kind smoke cluster.
# Subprocess helper for trap/cleanup verification (normal exit, error exit, SIGINT).

set -euo pipefail

MODE="${1:?mode required: normal|error|sigint}"
MARKER="${2:?marker file required}"
NAMESPACE="${3:-hpa-eval}"
CLUSTER_NAME="${4:-hpa-eval-smoke}"
PF_PORT="${5:-19090}"

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${LIB_DIR}/common.sh"
# shellcheck source=cleanup.sh
source "${LIB_DIR}/cleanup.sh"

TRAP_REASON="EXIT"

cleanup_on_exit() {
  cleanup_background_jobs
  echo "TRAP_FIRED reason=${TRAP_REASON}" >> "${MARKER}"
}

trap cleanup_on_exit EXIT
trap 'TRAP_REASON=INT; cleanup_on_exit; exit 130' INT
trap 'TRAP_REASON=TERM; cleanup_on_exit; exit 143' TERM

: > "${MARKER}"

kubectl port-forward "svc/prometheus" "${PF_PORT}:9090" -n "${NAMESPACE}" \
  --context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 &
pf_pid=$!
register_port_forward_pid "${pf_pid}"
echo "pf_pid=${pf_pid}" >> "${MARKER}"
echo "pf_port=${PF_PORT}" >> "${MARKER}"

if [[ "${MODE}" == "sigint" ]]; then
  while true; do
    sleep 1
  done
fi

sleep 2

if [[ "${MODE}" == "error" ]]; then
  exit 1
fi

exit 0
