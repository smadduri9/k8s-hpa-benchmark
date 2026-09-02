"""
Fill RESULTS.md placeholders from measured run artifacts.

Phase 2 operator step: run after GKE benchmark completes.
Missing evidence remains PENDING_RERUN or MISSING; nothing is estimated.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

MISSING = "MISSING"
PENDING = "PENDING_RERUN"


def load_locust(stats_path: str) -> dict:
    if not os.path.isfile(stats_path):
        return {"status": MISSING, "reason": "locust stats file absent"}
    with open(stats_path, newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("Name") == "Aggregated":
                requests = int(float(row.get("Request Count", 0)))
                failures = int(float(row.get("Failure Count", 0)))
                successes = requests - failures
                rate = failures / requests if requests else 0.0
                return {
                    "status": "MEASURED",
                    "requests": requests,
                    "failures": failures,
                    "successes": successes,
                    "failure_rate": rate,
                    "source": stats_path,
                }
    return {"status": MISSING, "reason": "no Aggregated row"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill RESULTS.md from measured artifacts")
    parser.add_argument("--run-root", required=True, help="results/runs/<run_id>")
    parser.add_argument("--rep", default="1")
    parser.add_argument("--results-md", default="RESULTS.md")
    args = parser.parse_args()

    rep_dir = Path(args.run_root) / f"rep-{args.rep}"
    fixed_stats = rep_dir / "locust_fixed_stats.csv"
    hpa_stats = rep_dir / "locust_hpa_stats.csv"

    fixed = load_locust(str(fixed_stats))
    hpa = load_locust(str(hpa_stats))

    summary = {
        "fixed_failure_rate": fixed,
        "hpa_failure_rate": hpa,
        "throughput_ratio": PENDING,
    }

    if fixed.get("status") == "MEASURED" and hpa.get("status") == "MEASURED":
        if fixed["successes"] > 0:
            summary["throughput_ratio"] = round(hpa["successes"] / fixed["successes"], 4)
        else:
            summary["throughput_ratio"] = MISSING

    out_path = Path(args.results_md)
    block = [
        "# RESULTS (auto-filled excerpt)",
        "",
        f"- fixed failure rate: {fixed.get('failure_rate', PENDING)} (source: {fixed.get('source', MISSING)})",
        f"- hpa failure rate: {hpa.get('failure_rate', PENDING)} (source: {hpa.get('source', MISSING)})",
        f"- throughput ratio (successful): {summary['throughput_ratio']}",
        "",
        "Cost and SLO sections remain PENDING_RERUN until Tier 2/3 modules run.",
        "",
    ]
    out_path.write_text("\n".join(block), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
