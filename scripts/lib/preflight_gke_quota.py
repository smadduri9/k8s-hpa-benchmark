#!/usr/bin/env python3
"""Parse GCP quota JSON and emit preflight table rows for GKE shape checks."""

from __future__ import annotations

import json
import re
import sys


def emit_quota_row(
    metric: str,
    error_name: str,
    limit: float,
    usage: float,
    required: float,
) -> bool:
    headroom = limit - usage
    if required > headroom:
        print(
            f"ERROR: {error_name} limit={limit:g} usage={usage:g} "
            f"required={required:g} headroom={headroom:g}",
            file=sys.stderr,
        )
        status = "FAIL"
        failed = True
    else:
        status = "PASS"
        failed = False
    print(
        f"GKE_QUOTA_{metric} limit={limit:g} usage={usage:g} "
        f"required={required:g} status={status}"
    )
    return failed


def check_regional(region_json_path: str, num_nodes: int, disk_gb: int, cpus_per_node: int) -> int:
    with open(region_json_path, encoding="utf-8") as handle:
        data = json.load(handle)

    quotas = {item["metric"]: item for item in data.get("quotas", [])}
    checks = [
        ("SSD_TOTAL_GB", "QUOTA_INSUFFICIENT_SSD", num_nodes * disk_gb),
        ("CPUS", "QUOTA_INSUFFICIENT_CPUS", num_nodes * cpus_per_node),
        ("INSTANCES", "QUOTA_INSUFFICIENT_INSTANCES", num_nodes),
    ]

    failed = False
    for metric, error_name, required in checks:
        entry = quotas.get(metric)
        if entry is None:
            print(f"GKE_QUOTA_{metric}=MISSING")
            print(f"ERROR: {error_name} quota metric {metric} not found in region", file=sys.stderr)
            failed = True
            continue
        limit = float(entry.get("limit", 0))
        usage = float(entry.get("usage", 0))
        if emit_quota_row(metric, error_name, limit, usage, required):
            failed = True

    return 1 if failed else 0


def machine_type_vcpus(machine_type: str) -> int | None:
    short = machine_type.rsplit("/", 1)[-1]
    match = re.match(r"e2-standard-(\d+)$", short)
    if match:
        return int(match.group(1))
    match = re.match(r"n2-standard-(\d+)$", short)
    if match:
        return int(match.group(1))
    return None


def running_instance_cpus(instances: list[dict]) -> tuple[int, list[tuple[str, str, int]]]:
    total = 0
    details: list[tuple[str, str, int]] = []
    for instance in instances:
        name = instance.get("name") or ""
        status = instance.get("status") or ""
        if status != "RUNNING":
            continue
        machine_type = instance.get("machineType") or ""
        vcpus = machine_type_vcpus(machine_type)
        if vcpus is None:
            raise ValueError(f"RUNNING_VM_CPU_UNKNOWN name={name} machineType={machine_type}")
        total += vcpus
        details.append((name, machine_type.rsplit("/", 1)[-1], vcpus))
    return total, details


def check_global_cpus(
    project_json_path: str,
    instances_json_path: str,
    cluster_cpus: int,
    runner_vm_name: str,
) -> int:
    with open(project_json_path, encoding="utf-8") as handle:
        project = json.load(handle)
    with open(instances_json_path, encoding="utf-8") as handle:
        instances = json.load(handle)

    quotas = {item["metric"]: item for item in project.get("quotas", [])}
    entry = quotas.get("CPUS_ALL_REGIONS")
    if entry is None:
        print("GKE_QUOTA_CPUS_ALL_REGIONS=MISSING")
        print(
            "ERROR: QUOTA_INSUFFICIENT_CPUS_ALL_REGIONS quota metric CPUS_ALL_REGIONS not found",
            file=sys.stderr,
        )
        return 1

    try:
        running_cpus, details = running_instance_cpus(instances)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for name, machine, vcpus in details:
        print(f"GKE_RUNNING_VM name={name} machine={machine} vcpus={vcpus}")

    runner_running = any(name == runner_vm_name for name, _, _ in details)
    if runner_running:
        print(
            f"ERROR: RUNNER_VM_MUST_BE_STOPPED vm={runner_vm_name} "
            f"reason=running_vm_consumes_global_cpus cluster_requires={cluster_cpus} "
            f"remediation=gcloud compute instances stop {runner_vm_name} --zone=<ZONE> --project=<PROJECT_ID>",
            file=sys.stderr,
        )
        return 1

    required = cluster_cpus + running_cpus
    limit = float(entry.get("limit", 0))
    usage = float(entry.get("usage", 0))
    failed = emit_quota_row("CPUS_ALL_REGIONS", "QUOTA_INSUFFICIENT_CPUS_ALL_REGIONS", limit, usage, required)
    if not failed:
        quota_headroom = limit - required
        if quota_headroom <= 0:
            print(
                f"GKE_QUOTA_CPUS_ALL_REGIONS_NO_HEADROOM limit={limit:g} "
                f"required={required:g} headroom={quota_headroom:g}"
            )
            print(
                "WARNING: CPUS_ALL_REGIONS has no headroom after cluster create; "
                "starting any other Compute Engine instance, including the runner VM "
                "(hpa-bench-runner), will break cluster creation",
                file=sys.stderr,
            )
    return 1 if failed else 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: preflight_gke_quota.py regional|global ...", file=sys.stderr)
        return 2

    mode = sys.argv[1]
    if mode == "regional":
        if len(sys.argv) != 6:
            print(
                "usage: preflight_gke_quota.py regional <region_json> <num_nodes> "
                "<disk_gb> <cpus_per_node>",
                file=sys.stderr,
            )
            return 2
        return check_regional(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]))

    if mode == "global":
        if len(sys.argv) != 6:
            print(
                "usage: preflight_gke_quota.py global <project_json> <instances_json> "
                "<cluster_cpus> <runner_vm_name>",
                file=sys.stderr,
            )
            return 2
        return check_global_cpus(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5])

    print(f"ERROR: unknown mode {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
