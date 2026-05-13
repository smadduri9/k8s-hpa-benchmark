"""
Generates realistic synthetic time-series data for the HPA evaluation experiments.

Simulates two scenarios:
  - fixed:  3 static replicas — latency spikes sharply during load spike, stays high
  - hpa:    HPA-managed replicas — latency spikes briefly, then recovers as pods scale up

Output: sample_data/fixed_metrics.csv and sample_data/hpa_metrics.csv

Usage:
  python3 analysis/simulate_results.py
"""

import os
import math
import random
import numpy as np
import csv
from datetime import datetime, timedelta

# Seed for reproducibility
rng = np.random.default_rng(42)

# ---------------------------------------------------------------------------
# Time axis (18 minutes at 15-second resolution)
# ---------------------------------------------------------------------------
STEP_SECONDS = 15
TOTAL_SECONDS = 18 * 60
TIMESTAMPS = [i for i in range(0, TOTAL_SECONDS + 1, STEP_SECONDS)]
N = len(TIMESTAMPS)

BASE_TIME = datetime(2026, 3, 17, 10, 0, 0)


def ts_to_datetime(t: int) -> str:
    return (BASE_TIME + timedelta(seconds=t)).isoformat()


# ---------------------------------------------------------------------------
# Load shape (mirrors locustfile.py phases)
# ---------------------------------------------------------------------------

def load_at(t: int) -> float:
    """Returns normalized load [0, 1] at time t (seconds)."""
    if t <= 180:
        return t / 180 * 0.25          # ramp-up: 0 → 0.25
    elif t <= 360:
        return 0.25 + (t - 180) / 180 * 0.75  # spike: 0.25 → 1.0
    elif t <= 900:
        return 1.0 - (t - 360) / 540 * 0.25   # sustained: 1.0 → 0.75
    else:
        return max(0.05, 0.75 - (t - 900) / 180 * 0.70)  # recovery


# ---------------------------------------------------------------------------
# Fixed deployment simulation
# ---------------------------------------------------------------------------

def simulate_fixed() -> list[dict]:
    rows = []
    for t in TIMESTAMPS:
        load = load_at(t)
        replicas = 3  # always 3

        # CPU utilization per pod — 3 pods share the load
        cpu_per_pod = min(95.0, load * 100.0 / replicas * 3.3)
        cpu_pct = cpu_per_pod + rng.normal(0, 2.0)
        cpu_pct = float(np.clip(cpu_pct, 0, 100))

        # Latency grows super-linearly as CPU saturates
        saturation = cpu_per_pod / 100.0
        if saturation < 0.6:
            p50 = 80 + saturation * 100
        else:
            # Exponential blow-up when saturated
            p50 = 80 + 60 + (saturation - 0.6) ** 2 * 3000

        p50 += rng.normal(0, p50 * 0.05)
        p95 = p50 * (1.5 + saturation * 0.8) + rng.normal(0, p50 * 0.08)
        p99 = p95 * (1.3 + saturation * 0.5) + rng.normal(0, p95 * 0.08)

        p50 = float(max(50, p50))
        p95 = float(max(p50 * 1.2, p95))
        p99 = float(max(p95 * 1.1, p99))

        # Requests per second — bounded by pod capacity
        max_rps = replicas * 12.0
        rps = min(load * 150, max_rps) + rng.normal(0, 1.5)
        rps = float(max(0, rps))

        # Error rate spikes when pods are overwhelmed
        if cpu_per_pod > 85:
            error_rate = (cpu_per_pod - 85) / 15 * 0.12
        else:
            error_rate = 0.001
        error_rate = float(np.clip(error_rate + rng.normal(0, 0.002), 0, 1))

        rows.append({
            "timestamp": ts_to_datetime(t),
            "elapsed_seconds": t,
            "experiment": "fixed",
            "replicas": replicas,
            "cpu_utilization_pct": round(cpu_pct, 2),
            "latency_p50_ms": round(p50, 1),
            "latency_p95_ms": round(p95, 1),
            "latency_p99_ms": round(p99, 1),
            "rps": round(rps, 2),
            "error_rate": round(error_rate, 4),
            "active_users": int(load * 200),
        })
    return rows


# ---------------------------------------------------------------------------
# HPA deployment simulation
# ---------------------------------------------------------------------------

def simulate_hpa() -> list[dict]:
    rows = []
    current_replicas = 1.0  # float for smooth interpolation
    target_replicas = 1.0

    for i, t in enumerate(TIMESTAMPS):
        load = load_at(t)

        # Determine target replicas based on load (HPA logic: scale to maintain 60% CPU)
        desired = max(1, min(10, math.ceil(load * 12 / 0.60)))

        # HPA reacts with ~90-second delay on scale-up, 60s stabilization on scale-down
        if desired > current_replicas:
            # Scale-up: lag of ~90 seconds (6 steps at 15s)
            if t >= 90:
                past_load = load_at(max(0, t - 90))
                past_desired = max(1, min(10, math.ceil(past_load * 12 / 0.60)))
                target_replicas = past_desired
        else:
            # Scale-down: stabilization window 60s (4 steps)
            if i >= 4:
                future_loads = [load_at(TIMESTAMPS[min(i + j, N - 1)]) for j in range(4)]
                future_desired = max(1, min(10, math.ceil(max(future_loads) * 12 / 0.60)))
                target_replicas = max(desired, future_desired)

        # Smooth replica count (pods don't appear instantly — ~30s startup)
        alpha = 0.3
        current_replicas = alpha * target_replicas + (1 - alpha) * current_replicas
        replica_count = max(1, round(current_replicas))

        # CPU utilization per pod
        cpu_per_pod = min(90.0, load * 100.0 / replica_count * 3.3)
        cpu_pct = cpu_per_pod + rng.normal(0, 2.0)
        cpu_pct = float(np.clip(cpu_pct, 0, 100))

        # Latency — better than fixed because replicas scale up
        saturation = cpu_per_pod / 100.0
        if saturation < 0.6:
            p50 = 80 + saturation * 80
        else:
            # Brief spike before HPA reacts
            p50 = 80 + 48 + (saturation - 0.6) ** 2 * 1500

        p50 += rng.normal(0, p50 * 0.05)
        p95 = p50 * (1.4 + saturation * 0.5) + rng.normal(0, p50 * 0.06)
        p99 = p95 * (1.25 + saturation * 0.3) + rng.normal(0, p95 * 0.07)

        p50 = float(max(50, p50))
        p95 = float(max(p50 * 1.2, p95))
        p99 = float(max(p95 * 1.1, p99))

        # RPS — more capacity with more replicas
        max_rps = replica_count * 12.0
        rps = min(load * 150, max_rps) + rng.normal(0, 1.5)
        rps = float(max(0, rps))

        error_rate = 0.001 if cpu_per_pod < 80 else float(np.clip((cpu_per_pod - 80) / 20 * 0.05, 0, 1))
        error_rate = float(np.clip(error_rate + rng.normal(0, 0.001), 0, 1))

        rows.append({
            "timestamp": ts_to_datetime(t),
            "elapsed_seconds": t,
            "experiment": "hpa",
            "replicas": replica_count,
            "cpu_utilization_pct": round(cpu_pct, 2),
            "latency_p50_ms": round(p50, 1),
            "latency_p95_ms": round(p95, 1),
            "latency_p99_ms": round(p99, 1),
            "rps": round(rps, 2),
            "error_rate": round(error_rate, 4),
            "active_users": int(load * 200),
        })
    return rows


# ---------------------------------------------------------------------------
# Write CSVs
# ---------------------------------------------------------------------------

FIELDNAMES = [
    "timestamp", "elapsed_seconds", "experiment", "replicas",
    "cpu_utilization_pct", "latency_p50_ms", "latency_p95_ms", "latency_p99_ms",
    "rps", "error_rate", "active_users",
]

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "sample_data")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def write_csv(rows: list[dict], filename: str):
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Wrote {len(rows)} rows -> {path}")


if __name__ == "__main__":
    print("Generating synthetic experiment data...")
    write_csv(simulate_fixed(), "fixed_metrics.csv")
    write_csv(simulate_hpa(), "hpa_metrics.csv")
    print("Done. Run 'python3 analysis/analyze_results.py' to generate figures.")
