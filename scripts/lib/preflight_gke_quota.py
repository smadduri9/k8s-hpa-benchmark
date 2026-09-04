#!/usr/bin/env python3
"""Parse regional quota JSON and emit preflight table rows for GKE shape checks."""

from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) != 5:
        print(
            "usage: preflight_gke_quota.py <region_json> <num_nodes> "
            "<disk_gb> <cpus_per_node>",
            file=sys.stderr,
        )
        return 2

    region_json_path = sys.argv[1]
    num_nodes = int(sys.argv[2])
    disk_gb = int(sys.argv[3])
    cpus_per_node = int(sys.argv[4])

    with open(region_json_path, encoding="utf-8") as handle:
        data = json.load(handle)

    quotas = {item["metric"]: item for item in data.get("quotas", [])}

    checks = [
        (
            "SSD_TOTAL_GB",
            "QUOTA_INSUFFICIENT_SSD",
            num_nodes * disk_gb,
        ),
        (
            "CPUS",
            "QUOTA_INSUFFICIENT_CPUS",
            num_nodes * cpus_per_node,
        ),
        (
            "INSTANCES",
            "QUOTA_INSUFFICIENT_INSTANCES",
            num_nodes,
        ),
    ]

    failed = False
    for metric, error_name, required in checks:
        entry = quotas.get(metric)
        if entry is None:
            print(f"GKE_QUOTA_{metric}=MISSING")
            print(f"ERROR: {error_name} quota metric {metric} not found in region")
            failed = True
            continue

        limit = float(entry.get("limit", 0))
        usage = float(entry.get("usage", 0))
        headroom = limit - usage
        if required > headroom:
            status = "FAIL"
            failed = True
            print(
                f"ERROR: {error_name} limit={limit:g} usage={usage:g} "
                f"required={required:g} headroom={headroom:g}",
                file=sys.stderr,
            )
        else:
            status = "PASS"

        print(
            f"GKE_QUOTA_{metric} limit={limit:g} usage={usage:g} "
            f"required={required:g} status={status}"
        )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
