#!/usr/bin/env python3
"""Derive SHAPE_MEAN_USERS from capacity-probe step observations."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from statistics import median

WC98_CONSTANT_MIN_UNIT = 0.9515588
WC98_FLASH_PEAK_TO_MEAN = 2.015377
CPU_REQ_CORES = 0.5
HPA_TARGET_UTIL = 0.60
MIN_REPLICAS = 4
HPA_TARGET_CLUSTER_CORES = MIN_REPLICAS * CPU_REQ_CORES * HPA_TARGET_UTIL
STOP_THRESHOLD_MILLICORES = 800
MISSING = "MISSING"

ERR_SATURATED_AT_START = "CAPACITY_PROBE_SATURATED_AT_START"
ERR_CPU_MISSING = "CAPACITY_PROBE_CPU_MISSING"
ERR_NO_FEASIBLE_U = "CAPACITY_PROBE_NO_FEASIBLE_U"
ERR_REPLICA_BELOW = "CAPACITY_PROBE_REPLICA_BELOW_DECLARED"


def parse_rps(raw: str) -> float | str:
    value = (raw or "").strip()
    if not value or value.upper() == "N/A":
        return MISSING
    try:
        return float(value)
    except ValueError:
        return MISSING


def parse_millicores(raw: str) -> int | str:
    value = (raw or "").strip()
    if not value or value == MISSING:
        return MISSING
    if value.endswith("m"):
        return int(value[:-1])
    # steps.csv column is median_millicores (integer millicores, no suffix).
    return int(float(value))


def read_steps(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {
            "step",
            "users",
            "rps",
            "median_millicores",
            "ready_pods",
            "stop_trigger",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"steps.csv missing required columns: {required}")
        for row in reader:
            rows.append(
                {
                    "step": int(row["step"]),
                    "users": int(row["users"]),
                    "rps": parse_rps(row["rps"]),
                    "median_millicores": parse_millicores(row["median_millicores"]),
                    "ready_pods": int(row["ready_pods"]),
                    "stop_trigger": (row["stop_trigger"] or "none").strip(),
                }
            )
    if not rows:
        raise ValueError("steps.csv is empty")
    return rows


def find_stopping_index(rows: list[dict]) -> int | None:
    for index, row in enumerate(rows):
        if row["stop_trigger"] != "none":
            return index
    return None


def compute_rates(pre_sat: dict) -> dict:
    users = pre_sat["users"]
    rps = pre_sat["rps"]
    median_m = pre_sat["median_millicores"]
    ready = pre_sat["ready_pods"]

    if ready != MIN_REPLICAS:
        raise SystemExit(
            f"ERROR: {ERR_REPLICA_BELOW} ready_pods={ready} declared={MIN_REPLICAS}"
        )

    if median_m is MISSING:
        raise SystemExit(f"ERROR: {ERR_CPU_MISSING} step={pre_sat['step']}")

    if rps is MISSING or users <= 0:
        rps_per_user = MISSING
    else:
        rps_per_user = rps / users

    median_cpu_cores = median_m / 1000.0
    cluster_cpu_cores = median_cpu_cores * ready

    if rps is MISSING or rps == 0:
        cpu_per_rps = MISSING
    else:
        cpu_per_rps = cluster_cpu_cores / rps

    return {
        "users": users,
        "rps": rps,
        "median_millicores": median_m,
        "ready_pods": ready,
        "rps_per_user": rps_per_user,
        "median_cpu_cores": median_cpu_cores,
        "cluster_cpu_cores": cluster_cpu_cores,
        "cpu_per_rps": cpu_per_rps,
    }


def estimate_cluster_cores(users: int, rps_per_user: float, cpu_per_rps: float) -> float:
    return users * rps_per_user * cpu_per_rps


def scan_shape_mean_users(rps_per_user: float, cpu_per_rps: float) -> dict:
    evaluations: list[dict] = []
    feasible: list[int] = []

    for u in range(1, 5001):
        constant_users = round(WC98_CONSTANT_MIN_UNIT * u)
        flash_users = round(WC98_FLASH_PEAK_TO_MEAN * u)
        est_constant = estimate_cluster_cores(constant_users, rps_per_user, cpu_per_rps)
        est_flash = estimate_cluster_cores(flash_users, rps_per_user, cpu_per_rps)
        warm_ok = est_constant < HPA_TARGET_CLUSTER_CORES
        flash_ok = est_flash > HPA_TARGET_CLUSTER_CORES
        evaluations.append(
            {
                "U": u,
                "constant_users": constant_users,
                "flash_users": flash_users,
                "est_constant_cluster_cores": est_constant,
                "est_flash_cluster_cores": est_flash,
                "warmup_below_target": warm_ok,
                "flash_above_target": flash_ok,
            }
        )
        if warm_ok and flash_ok:
            feasible.append(u)

    return {
        "evaluations": evaluations,
        "feasible_U": feasible,
        "max_U_warmup_only": max(
            (e["U"] for e in evaluations if e["warmup_below_target"]),
            default=None,
        ),
        "min_U_flash_only": min(
            (e["U"] for e in evaluations if e["flash_above_target"]),
            default=None,
        ),
        "SHAPE_MEAN_USERS": max(feasible) if feasible else None,
    }


def derive_from_steps(rows: list[dict]) -> dict:
    stopping_index = find_stopping_index(rows)
    if stopping_index is None:
        raise SystemExit("ERROR: CAPACITY_PROBE_NO_STOP_STEP")

    if stopping_index == 0:
        raise SystemExit(f"ERROR: {ERR_SATURATED_AT_START}")

    pre_sat = rows[stopping_index - 1]
    rates = compute_rates(pre_sat)

    if rates["rps_per_user"] is MISSING or rates["cpu_per_rps"] is MISSING:
        raise SystemExit(
            f"ERROR: CAPACITY_PROBE_RATES_MISSING step={pre_sat['step']} "
            f"rps={rates['rps']} users={rates['users']}"
        )

    scan = scan_shape_mean_users(rates["rps_per_user"], rates["cpu_per_rps"])
    result = {
        "pre_saturation_step": pre_sat["step"],
        "stopping_step": rows[stopping_index]["step"],
        "stop_trigger": rows[stopping_index]["stop_trigger"],
        "rates": rates,
        "constants": {
            "wc98_constant_min_unit": WC98_CONSTANT_MIN_UNIT,
            "wc98_flash_peak_to_mean": WC98_FLASH_PEAK_TO_MEAN,
            "cpu_req_cores": CPU_REQ_CORES,
            "hpa_target_util": HPA_TARGET_UTIL,
            "min_replicas": MIN_REPLICAS,
            "hpa_target_cluster_cores": HPA_TARGET_CLUSTER_CORES,
            "stop_threshold_millicores": STOP_THRESHOLD_MILLICORES,
        },
        "scan": scan,
    }

    if scan["SHAPE_MEAN_USERS"] is None:
        raise SystemExit(
            f"ERROR: {ERR_NO_FEASIBLE_U} "
            f"max_U_warmup_only={scan['max_U_warmup_only']} "
            f"min_U_flash_only={scan['min_U_flash_only']}"
        )

    result["SHAPE_MEAN_USERS"] = scan["SHAPE_MEAN_USERS"]
    return result


def median_millicores_from_kubectl_top(text: str) -> int | str:
    values: list[int] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        parsed = parse_millicores(parts[1])
        if parsed is not MISSING:
            values.append(parsed)
    if not values:
        return MISSING
    return int(median(values))


def read_locust_rps(stats_csv: Path) -> float | str:
    if not stats_csv.is_file():
        return MISSING
    with stats_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("Name") != "Aggregated":
                continue
            return parse_rps(row.get("Requests/s", ""))
    return MISSING


def median_ready_pod_millicores(pod_lines: list[tuple[str, str]]) -> int | str:
    values: list[int] = []
    for _name, cpu in pod_lines:
        parsed = parse_millicores(cpu)
        if parsed is not MISSING:
            values.append(parsed)
    if not values:
        return MISSING
    return int(median(values))


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "top-median":
        text = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()
        print(median_millicores_from_kubectl_top(text))
        return 0

    parser = argparse.ArgumentParser(description="Derive SHAPE_MEAN_USERS from probe steps.")
    parser.add_argument("--steps", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    rows = read_steps(args.steps)
    result = derive_from_steps(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"SHAPE_MEAN_USERS={result['SHAPE_MEAN_USERS']}")
    print(f"CAPACITY_PROBE_DERIVE_PASS out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
