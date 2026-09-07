#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, gcloud CLI.
# Provision a same-zone GCE runner VM for unattended run_benchmark.sh sessions.
# Image builds stay on the Mac (deploy_gke.sh); this VM has no Docker.
#
# Usage: bash scripts/provision_runner_vm.sh [--env-file .env]
#
# Operator-only: this script creates billable GCP resources. The benchmark agent
# writes it but must not execute gcloud compute instances create.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

ENV_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    -h|--help)
      cat <<'EOF'
Usage: bash scripts/provision_runner_vm.sh [--env-file .env]

Creates an e2-standard-4 VM (50 GB boot disk) in ZONE from .env.
ZONE must match the GKE cluster zone. Optional RUNNER_VM_NAME (default hpa-bench-runner).

After create, bind the VM's default compute service account:
  gcloud projects add-iam-policy-binding PROJECT_ID \
    --member="serviceAccount:NUMERIC_PROJECT_ID-compute@developer.gserviceaccount.com" \
    --role="roles/container.developer"

Then SSH, clone the repo, python3.14 -m venv .venv, pip install -r requirements-tooling.txt,
gcloud container clusters get-credentials, and run benchmarks inside tmux session hpa-bench.
EOF
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

load_env_file "${ENV_FILE}"
require_env PROJECT_ID
require_env ZONE
require_env CLUSTER_NAME

RUNNER_VM_NAME="${RUNNER_VM_NAME:-hpa-bench-runner}"
MACHINE_TYPE="e2-standard-4"
BOOT_DISK_SIZE_GB="50"
IMAGE_FAMILY="${RUNNER_IMAGE_FAMILY:-ubuntu-2404-lts-amd64}"
IMAGE_PROJECT="${RUNNER_IMAGE_PROJECT:-ubuntu-os-cloud}"

if ! command -v gcloud >/dev/null 2>&1; then
  die "gcloud is required to provision the runner VM"
fi

active_project="$(gcloud config get-value project 2>/dev/null || true)"
if [[ -n "${active_project}" && "${active_project}" != "${PROJECT_ID}" ]]; then
  echo "WARNING: gcloud active project=${active_project} differs from PROJECT_ID=${PROJECT_ID}" >&2
  echo "WARNING: all commands below use --project=${PROJECT_ID}" >&2
fi

cat <<EOF
RUNNER_VM_PLAN
  project=${PROJECT_ID}
  zone=${ZONE}
  name=${RUNNER_VM_NAME}
  machine_type=${MACHINE_TYPE}
  boot_disk_gb=${BOOT_DISK_SIZE_GB}
  cluster=${CLUSTER_NAME}
  iam_role=roles/container.developer
  docker=absent reason=runner_vm_builds_on_mac
EOF

cat <<'EOF'

IAM (operator runs manually; not applied by this script):
  The VM uses the project default compute service account. Grant container.developer
  so kubectl can reach the GKE cluster after get-credentials:
    SA="$(gcloud iam service-accounts list --project=PROJECT_ID \
      --filter='email~compute@developer.gserviceaccount.com' --format='value(email)' | head -n1)"
    gcloud projects add-iam-policy-binding PROJECT_ID \
      --member="serviceAccount:${SA}" \
      --role="roles/container.developer"

Post-create on the VM (bootstrap startup-script installs git, kubectl, rsync, tmux, Python 3.14):
  git clone <repo-url> k8s-hpa-benchmark && cd k8s-hpa-benchmark
  python3.14 -m venv .venv
  ".venv/bin/python" -m pip install -r requirements-tooling.txt
  gcloud container clusters get-credentials CLUSTER_NAME --zone ZONE --project PROJECT_ID
  tmux new -s hpa-bench
  bash scripts/run_benchmark.sh --env-file .env --repetitions 1

Pull results from the Mac after STATUS is written:
  rsync -avz RUNNER_USER@RUNNER_HOST:~/k8s-hpa-benchmark/results/runs/<run_id>/ results/runs/<run_id>/

EOF

startup_script="$(cat <<'BOOT'
#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git rsync tmux curl ca-certificates gnupg apt-transport-https software-properties-common

if ! command -v kubectl >/dev/null 2>&1; then
  curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.35/deb/Release.key \
    | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
  echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.35/deb/ /' \
    > /etc/apt/sources.list.d/kubernetes.list
  apt-get update -y
  apt-get install -y kubectl
fi

if ! command -v python3.14 >/dev/null 2>&1; then
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update -y
  apt-get install -y python3.14 python3.14-venv python3.14-dev
fi

if ! command -v gcloud >/dev/null 2>&1; then
  curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
    | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
    > /etc/apt/sources.list.d/google-cloud-sdk.list
  apt-get update -y
  apt-get install -y google-cloud-cli
fi

echo "RUNNER_VM_BOOTSTRAP_COMPLETE packages=git,kubectl,rsync,tmux,python3.14,gcloud"
BOOT
)"

startup_file="$(mktemp)"
trap 'rm -f "${startup_file}"' EXIT
printf '%s\n' "${startup_script}" > "${startup_file}"

echo "Creating VM ${RUNNER_VM_NAME} in ${ZONE} (project ${PROJECT_ID})..."
gcloud compute instances create "${RUNNER_VM_NAME}" \
  --project="${PROJECT_ID}" \
  --zone="${ZONE}" \
  --machine-type="${MACHINE_TYPE}" \
  --boot-disk-size="${BOOT_DISK_SIZE_GB}GB" \
  --boot-disk-type=pd-balanced \
  --image-family="${IMAGE_FAMILY}" \
  --image-project="${IMAGE_PROJECT}" \
  --scopes=cloud-platform \
  --metadata-from-file=startup-script="${startup_file}"

gcloud compute instances describe "${RUNNER_VM_NAME}" \
  --project="${PROJECT_ID}" \
  --zone="${ZONE}" \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'

echo "RUNNER_VM_CREATED name=${RUNNER_VM_NAME} zone=${ZONE}"
