#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, docker, gcloud, locust, python3.
# Strict preflight checks. Never infers GCP project from gcloud config.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

ENV_FILE=""
REQUIRE_GKE=false

usage() {
  cat <<'EOF'
Usage: bash scripts/preflight.sh [--env-file .env] [--require-gke]

Auto-detects and hard-fails on missing/incompatible toolchain.
Requires explicit PROJECT_ID, REGION, CLUSTER_NAME, ARTIFACT_REGISTRY_REPO when --require-gke is set.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --require-gke)
      REQUIRE_GKE=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

load_env_file "${ENV_FILE}"

OS_NAME="$(uname -s)"
ARCH_NAME="$(uname -m)"
echo "PREFLIGHT_TABLE_BEGIN"
echo "os=${OS_NAME}"
echo "arch=${ARCH_NAME}"

if [[ "${OS_NAME}" != "Darwin" && "${OS_NAME}" != "Linux" ]]; then
  die "unsupported OS: ${OS_NAME}"
fi

# GNU vs BSD tool checks
if sed --version >/dev/null 2>&1; then
  echo "sed=GNU"
else
  echo "sed=BSD"
fi

if date -d '@0' >/dev/null 2>&1; then
  echo "date=GNU"
else
  echo "date=BSD"
fi

first_line() {
  local text="$1"
  printf '%s' "${text%%$'\n'*}"
}

for cmd in gcloud docker python3; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    die "missing required command: ${cmd}"
  fi
  version="$(first_line "$(${cmd} --version 2>&1)")"
  echo "${cmd}=${version}"
done

if command -v kubectl >/dev/null 2>&1; then
  echo "kubectl=$(kubectl version --client=true 2>&1 | awk '/Client Version:/{print $0; exit}')"
else
  die "missing required command: kubectl"
fi

if command -v locust >/dev/null 2>&1; then
  echo "locust=$(first_line "$(locust --version 2>&1)")"
elif python3 -m locust --version >/dev/null 2>&1; then
  echo "locust=$(first_line "$(python3 -m locust --version 2>&1)") [python -m]"
else
  die "missing required command: locust (or python3 -m locust)"
fi

client_ver="$(kubectl version --client=true -o yaml 2>/dev/null | awk '/gitVersion:/{print $2; exit}')"
if kubectl cluster-info >/dev/null 2>&1; then
  server_ver="$(kubectl version --output=yaml 2>/dev/null | awk '/serverVersion:/{f=1} f&&/gitVersion:/{print $2; exit}')"
  echo "kubectl_client=${client_ver}"
  echo "kubectl_server=${server_ver}"
else
  echo "kubectl_client=${client_ver}"
  echo "kubectl_server=UNAVAILABLE"
fi

host_arch="${ARCH_NAME}"
if [[ "${host_arch}" == "arm64" || "${host_arch}" == "aarch64" ]]; then
  echo "docker_platform_check=linux/amd64 required for typical GKE nodes"
fi

if [[ "${REQUIRE_GKE}" == "true" ]]; then
  require_env PROJECT_ID
  require_env REGION
  require_env CLUSTER_NAME
  require_env ARTIFACT_REGISTRY_REPO
  echo "PROJECT_ID=${PROJECT_ID}"
  echo "REGION=${REGION}"
  echo "CLUSTER_NAME=${CLUSTER_NAME}"
  echo "ARTIFACT_REGISTRY_REPO=${ARTIFACT_REGISTRY_REPO}"
  echo "PROJECT_CLUSTER_VERIFICATION_REQUIRED"
fi

echo "PREFLIGHT_PASS"
echo "PREFLIGHT_TABLE_END"
