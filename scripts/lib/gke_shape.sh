#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+.
# Shared GKE cluster shape constants for deploy_gke.sh and preflight quota checks.

# Fixed node count — no cluster autoscaler (see deploy_gke.sh arithmetic).
GKE_NUM_NODES="${GKE_NUM_NODES:-3}"

# Balanced PD boot disks (GKE 1.24+ default) count against SSD_TOTAL_GB, not DISKS_TOTAL_GB.
# hpa-benchmark-2026 us-central1 SSD_TOTAL_GB limit is 250; 3×50=150 leaves headroom.
NODE_DISK_SIZE_GB="${NODE_DISK_SIZE_GB:-50}"

GKE_MACHINE_TYPE="${GKE_MACHINE_TYPE:-e2-standard-2}"
# e2-standard-2 vCPU count (used for CPUS quota: NUM_NODES * GKE_CPUS_PER_NODE).
GKE_CPUS_PER_NODE=2
