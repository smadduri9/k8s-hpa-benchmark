#!/usr/bin/env python3
"""Verify HPA maxReplicas fits measured schedulable CPU on the live cluster."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

CHECK_NAME = "HPA_MAX_REPLICAS_SCHEDULABLE_GATE"
FAIL_ERROR = "HPA_MAX_REPLICAS_SCHEDULABLE_EXCEEDED"
# Minimum headroom on usable CPU after system pods and Prometheus.
SAFETY_MARGIN_RATIO = 0.10


def parse_cpu_millicores(value: str) -> int:
    if value.endswith("m"):
        return int(value[:-1])
    return int(float(value) * 1000)


def yaml_scalar(path: Path, key: str) -> str:
    pattern = re.compile(rf'^\s*{re.escape(key)}:\s*"?([^"\s]+)"?\s*$')
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1)
    raise ValueError(f"{key} not found in {path}")


def kubectl_json(args: list[str]) -> dict:
    proc = subprocess.run(
        ["kubectl", *args, "-o", "json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(proc.stderr.strip(), file=sys.stderr)
        raise RuntimeError(f"kubectl failed: {' '.join(args)}")
    return json.loads(proc.stdout)


def container_cpu_requests_millicores(pod: dict) -> int:
    total = 0
    for container in pod.get("spec", {}).get("containers", []):
        cpu = container.get("resources", {}).get("requests", {}).get("cpu")
        if cpu:
            total += parse_cpu_millicores(cpu)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Allocatable gate for HPA maxReplicas.")
    parser.add_argument("--namespace", default="hpa-eval")
    parser.add_argument("--hpa-manifest", type=Path, required=True)
    parser.add_argument("--fixed-manifest", type=Path, required=True)
    parser.add_argument("--prometheus-manifest", type=Path, required=True)
    args = parser.parse_args()

    max_replicas = int(yaml_scalar(args.hpa_manifest, "maxReplicas"))
    fixed_replicas = int(yaml_scalar(args.fixed_manifest, "replicas"))
    pod_cpu_request = parse_cpu_millicores(yaml_scalar(args.fixed_manifest, "cpu"))

    nodes = kubectl_json(["get", "nodes"])
    total_allocatable = 0
    for node in nodes.get("items", []):
        cpu = node.get("status", {}).get("allocatable", {}).get("cpu")
        if not cpu:
            print("ERROR: node missing status.allocatable.cpu", file=sys.stderr)
            return 1
        total_allocatable += parse_cpu_millicores(cpu)

    pods = kubectl_json(["get", "pods", "-A"])
    system_cpu_requests = 0
    prometheus_cpu_requests = 0
    for pod in pods.get("items", []):
        cpu = container_cpu_requests_millicores(pod)
        namespace = pod.get("metadata", {}).get("namespace", "")
        if namespace == args.namespace:
            if pod.get("metadata", {}).get("labels", {}).get("app") == "prometheus":
                prometheus_cpu_requests += cpu
            continue
        system_cpu_requests += cpu

    usable_cpu = total_allocatable - system_cpu_requests - prometheus_cpu_requests
    peak_app_cpu = (max_replicas + fixed_replicas) * pod_cpu_request
    allowed_peak = int(usable_cpu * (1.0 - SAFETY_MARGIN_RATIO))

    print(f"NODE_ALLOCATABLE_CPU_TOTAL={total_allocatable}m")
    print(f"SYSTEM_POD_CPU_REQUESTS={system_cpu_requests}m")
    print(f"PROMETHEUS_CPU_REQUESTS={prometheus_cpu_requests}m")
    print(f"USABLE_CPU={usable_cpu}m")
    print(
        f"PEAK_APP_CPU_REQUESTS={peak_app_cpu}m "
        f"(maxReplicas={max_replicas} fixedReplicas={fixed_replicas} "
        f"podCpuRequest={pod_cpu_request}m)"
    )
    print(f"SCHEDULABLE_CPU_SAFETY_MARGIN_RATIO={SAFETY_MARGIN_RATIO}")
    print(f"ALLOWED_PEAK_CPU={allowed_peak}m")

    if peak_app_cpu > allowed_peak:
        headroom = usable_cpu - peak_app_cpu
        print(f"{CHECK_NAME}=FAIL", file=sys.stderr)
        print(
            f"ERROR: {FAIL_ERROR} peak={peak_app_cpu}m allowed={allowed_peak}m "
            f"usable={usable_cpu}m headroom={headroom}m "
            f"safety_margin_ratio={SAFETY_MARGIN_RATIO}",
            file=sys.stderr,
        )
        return 1

    headroom = usable_cpu - peak_app_cpu
    margin_pct = (headroom / usable_cpu * 100.0) if usable_cpu else 0.0
    print(f"{CHECK_NAME}=PASS headroom={headroom}m margin_pct={margin_pct:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
