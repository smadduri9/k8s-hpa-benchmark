"""
Ingest Locust CSV stats as authoritative request/success/failure evidence.

Authority split:
  - Locust is authoritative for request counts, successes, and failures.
  - Prometheus is authoritative for replicas, CPU, and timing.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

MISSING = "MISSING"


def parse_locust_stats(path: str) -> dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("Name") == "Aggregated":
                request_count = int(float(row.get("Request Count", 0)))
                failure_count = int(float(row.get("Failure Count", 0)))
                success_count = request_count - failure_count
                failure_rate = (failure_count / request_count) if request_count else 0.0
                return {
                    "source": path,
                    "request_count": request_count,
                    "failure_count": failure_count,
                    "success_count": success_count,
                    "failure_rate": failure_rate,
                }

    raise ValueError(f"No Aggregated row found in {path}")


def ingest(arm: str, stats_path: str) -> dict:
    stats = parse_locust_stats(stats_path)
    stats["arm"] = arm
    stats["authority"] = "LOCUST"
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Locust stats for both arms")
    parser.add_argument("--fixed-stats", required=True)
    parser.add_argument("--hpa-stats", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if not os.path.isfile(args.fixed_stats):
        print("ASSERTION FAILED: locust_fixed_stats.csv is absent", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(args.hpa_stats):
        print("ASSERTION FAILED: locust_hpa_stats.csv is absent", file=sys.stderr)
        sys.exit(1)

    fixed = ingest("fixed", args.fixed_stats)
    hpa = ingest("hpa", args.hpa_stats)

    payload = {
        "REQUEST_AUTHORITY": "LOCUST",
        "PROM_AUTHORITY": "REPLICAS_CPU_TIMING",
        "fixed": fixed,
        "hpa": hpa,
    }

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    print("LOCUST_FIXED_STATS_FOUND")
    print("LOCUST_HPA_STATS_FOUND")
    print("REQUEST_AUTHORITY=LOCUST")
    print("PROM_AUTHORITY=REPLICAS_CPU_TIMING")


if __name__ == "__main__":
    main()
