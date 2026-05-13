"""
Reads fixed_metrics.csv and hpa_metrics.csv and generates publication-quality figures.

Outputs 4 PNG plots to sample_data/figures/:
  1. latency_comparison.png   — p50/p95/p99 over time, Fixed vs HPA
  2. throughput_comparison.png — RPS over time
  3. cpu_replicas.png          — CPU utilization + replica count (HPA only)
  4. cost_performance.png      — Pod-hours × cost bar chart

Also prints a statistical summary table.

Usage:
  python3 analysis/analyze_results.py
  # or with custom paths:
  python3 analysis/analyze_results.py --fixed path/to/fixed.csv --hpa path/to/hpa.csv
"""

import argparse
import csv
import os
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec

# ---------------------------------------------------------------------------
# Colors and style
# ---------------------------------------------------------------------------
COLORS = {
    "fixed_p50":  "#2166ac",
    "fixed_p95":  "#4393c3",
    "fixed_p99":  "#92c5de",
    "hpa_p50":    "#d6604d",
    "hpa_p95":    "#f4a582",
    "hpa_p99":    "#fddbc7",
    "hpa_cpu":    "#e08214",
    "hpa_rep":    "#d6604d",
    "users":      "#666666",
}

PHASE_BOUNDARIES = [0, 180, 360, 900, 1080]
PHASE_LABELS     = ["Ramp-up", "Spike", "Sustained", "Recovery"]
PHASE_COLORS     = ["#f7f7f7", "#fee8c8", "#edf8e9", "#deebf7"]


def phase_bands(ax, max_y: float):
    """Draw colored phase background bands."""
    for i, (start, end) in enumerate(zip(PHASE_BOUNDARIES, PHASE_BOUNDARIES[1:])):
        ax.axvspan(start, end, alpha=0.15, color=PHASE_COLORS[i], zorder=0)
        mid = (start + end) / 2
        ax.text(mid, max_y * 0.97, PHASE_LABELS[i],
                ha="center", va="top", fontsize=7, color="#555555",
                style="italic")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_csv(path: str) -> list[dict]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            converted = {}
            for k, v in row.items():
                try:
                    converted[k] = float(v) if v != "" else None
                except ValueError:
                    converted[k] = v
            rows.append(converted)
    return rows


def extract(rows: list[dict], key: str) -> tuple[np.ndarray, np.ndarray]:
    t = np.array([r["elapsed_seconds"] for r in rows])
    v = np.array([r[key] if r[key] is not None else np.nan for r in rows])
    return t, v


# ---------------------------------------------------------------------------
# Figure 1 — Latency Comparison
# ---------------------------------------------------------------------------

def fig_latency(fixed: list[dict], hpa: list[dict], out_dir: Path):
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=True)
    fig.suptitle("Response Latency Over Time: Fixed vs HPA", fontsize=13, fontweight="bold")

    max_y = 1100

    for ax, rows, title, prefix in [
        (axes[0], fixed, "Fixed Deployment (3 replicas)", "fixed"),
        (axes[1], hpa,   "HPA Deployment (1–10 replicas)", "hpa"),
    ]:
        t, p50 = extract(rows, "latency_p50_ms")
        _, p95  = extract(rows, "latency_p95_ms")
        _, p99  = extract(rows, "latency_p99_ms")

        phase_bands(ax, max_y)
        ax.plot(t / 60, p99, lw=1.5, alpha=0.6, color=COLORS[f"{prefix}_p99"], label="p99")
        ax.plot(t / 60, p95, lw=2.0, alpha=0.8, color=COLORS[f"{prefix}_p95"], label="p95")
        ax.plot(t / 60, p50, lw=2.5, color=COLORS[f"{prefix}_p50"], label="p50")

        ax.set_title(title, fontsize=11)
        ax.set_xlabel("Time (minutes)", fontsize=10)
        ax.set_ylabel("Latency (ms)", fontsize=10) if ax == axes[0] else None
        ax.legend(loc="upper left", fontsize=9)
        ax.set_xlim(0, 18)
        ax.set_ylim(0, max_y)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    path = out_dir / "latency_comparison.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved {path}")


# ---------------------------------------------------------------------------
# Figure 2 — Throughput Comparison
# ---------------------------------------------------------------------------

def fig_throughput(fixed: list[dict], hpa: list[dict], out_dir: Path):
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.set_title("Request Throughput (RPS) Over Time: Fixed vs HPA", fontsize=13, fontweight="bold")

    tf, rps_f = extract(fixed, "rps")
    th, rps_h = extract(hpa,   "rps")

    max_y = max(np.nanmax(rps_f), np.nanmax(rps_h)) * 1.15
    phase_bands(ax, max_y)

    ax.plot(tf / 60, rps_f, lw=2.5, color=COLORS["fixed_p50"], label="Fixed (3 replicas)")
    ax.plot(th / 60, rps_h, lw=2.5, color=COLORS["hpa_p50"],   label="HPA (1–10 replicas)")

    ax.set_xlabel("Time (minutes)", fontsize=10)
    ax.set_ylabel("Requests per Second", fontsize=10)
    ax.legend(fontsize=10)
    ax.set_xlim(0, 18)
    ax.set_ylim(0, max_y)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    path = out_dir / "throughput_comparison.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved {path}")


# ---------------------------------------------------------------------------
# Figure 3 — CPU Utilization + Replica Count
# ---------------------------------------------------------------------------

def fig_cpu_replicas(hpa: list[dict], out_dir: Path):
    fig, ax1 = plt.subplots(figsize=(12, 5))
    ax2 = ax1.twinx()

    ax1.set_title("HPA Scaling Behavior: CPU Utilization vs Replica Count", fontsize=13, fontweight="bold")

    t, cpu  = extract(hpa, "cpu_utilization_pct")
    _, reps = extract(hpa, "replicas")

    max_cpu = 110
    phase_bands(ax1, max_cpu)

    ax1.plot(t / 60, cpu, lw=2.0, color=COLORS["hpa_cpu"], label="CPU Utilization (%)")
    ax1.axhline(60, color=COLORS["hpa_cpu"], lw=1.0, linestyle="--", alpha=0.6, label="HPA Target (60%)")
    ax2.step(t / 60, reps, lw=2.5, color=COLORS["hpa_rep"], where="post", label="Replica Count")

    ax1.set_xlabel("Time (minutes)", fontsize=10)
    ax1.set_ylabel("CPU Utilization (%)", color=COLORS["hpa_cpu"], fontsize=10)
    ax2.set_ylabel("Replica Count", color=COLORS["hpa_rep"], fontsize=10)
    ax1.set_xlim(0, 18)
    ax1.set_ylim(0, max_cpu)
    ax2.set_ylim(0, 12)
    ax1.grid(True, alpha=0.3)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=9)

    plt.tight_layout()
    path = out_dir / "cpu_replicas.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved {path}")


# ---------------------------------------------------------------------------
# Figure 4 — Cost-Performance Bar Chart
# ---------------------------------------------------------------------------

def fig_cost_performance(fixed: list[dict], hpa: list[dict], out_dir: Path):
    # Approximate pod-hours and cost
    # GKE e2-standard-2: ~$0.0535/hour; each pod uses ~0.1 vCPU and ~128 MB
    COST_PER_POD_HOUR = 0.0535 * (0.1 / 2)  # fraction of node cost

    duration_hours = 18 / 60

    # Fixed: always 3 pods
    fixed_pod_hours = 3 * duration_hours
    fixed_cost = fixed_pod_hours * COST_PER_POD_HOUR

    # HPA: average replica count × duration
    _, reps_h = extract(hpa, "replicas")
    hpa_avg_replicas = float(np.nanmean(reps_h))
    hpa_pod_hours = hpa_avg_replicas * duration_hours
    hpa_cost = hpa_pod_hours * COST_PER_POD_HOUR

    # Mean p95 latency as performance proxy
    _, p95_f = extract(fixed, "latency_p95_ms")
    _, p95_h = extract(hpa,   "latency_p95_ms")
    mean_p95_fixed = float(np.nanmean(p95_f))
    mean_p95_hpa   = float(np.nanmean(p95_h))

    # Cost per "good request" approximation
    _, rps_f = extract(fixed, "rps")
    _, rps_h = extract(hpa,   "rps")
    total_requests_fixed = float(np.nansum(rps_f)) * 15
    total_requests_hpa   = float(np.nansum(rps_h)) * 15
    cost_per_kreq_fixed  = fixed_cost / (total_requests_fixed / 1000) if total_requests_fixed else 0
    cost_per_kreq_hpa    = hpa_cost   / (total_requests_hpa   / 1000) if total_requests_hpa   else 0

    fig, axes = plt.subplots(1, 3, figsize=(13, 5))
    fig.suptitle("Cost vs Performance Summary", fontsize=13, fontweight="bold")

    bar_kw = dict(width=0.5, edgecolor="black", linewidth=0.7)
    experiments = ["Fixed", "HPA"]
    x = [0, 1]

    # Pod-hours
    axes[0].bar(x, [fixed_pod_hours, hpa_pod_hours], color=[COLORS["fixed_p50"], COLORS["hpa_p50"]], **bar_kw)
    axes[0].set_xticks(x); axes[0].set_xticklabels(experiments)
    axes[0].set_title("Pod-Hours Used", fontsize=11)
    axes[0].set_ylabel("Pod-hours"); axes[0].grid(True, axis="y", alpha=0.3)
    for xi, v in zip(x, [fixed_pod_hours, hpa_pod_hours]):
        axes[0].text(xi, v + 0.002, f"{v:.3f}", ha="center", fontsize=9)

    # Mean p95 latency
    axes[1].bar(x, [mean_p95_fixed, mean_p95_hpa], color=[COLORS["fixed_p50"], COLORS["hpa_p50"]], **bar_kw)
    axes[1].set_xticks(x); axes[1].set_xticklabels(experiments)
    axes[1].set_title("Mean p95 Latency (ms)", fontsize=11)
    axes[1].set_ylabel("Milliseconds"); axes[1].grid(True, axis="y", alpha=0.3)
    for xi, v in zip(x, [mean_p95_fixed, mean_p95_hpa]):
        axes[1].text(xi, v + 5, f"{v:.0f}ms", ha="center", fontsize=9)

    # Cost per 1k requests
    axes[2].bar(x, [cost_per_kreq_fixed, cost_per_kreq_hpa], color=[COLORS["fixed_p50"], COLORS["hpa_p50"]], **bar_kw)
    axes[2].set_xticks(x); axes[2].set_xticklabels(experiments)
    axes[2].set_title("Cost per 1k Requests ($)", fontsize=11)
    axes[2].set_ylabel("USD"); axes[2].grid(True, axis="y", alpha=0.3)
    for xi, v in zip(x, [cost_per_kreq_fixed, cost_per_kreq_hpa]):
        axes[2].text(xi, v + 0.000005, f"${v:.5f}", ha="center", fontsize=9)

    plt.tight_layout()
    path = out_dir / "cost_performance.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved {path}")


# ---------------------------------------------------------------------------
# Statistical summary table
# ---------------------------------------------------------------------------

def print_summary(fixed: list[dict], hpa: list[dict]):
    metrics = [
        ("latency_p50_ms", "Latency p50 (ms)"),
        ("latency_p95_ms", "Latency p95 (ms)"),
        ("latency_p99_ms", "Latency p99 (ms)"),
        ("rps",            "Throughput (RPS)"),
        ("cpu_utilization_pct", "CPU Util (%)"),
        ("replicas",       "Replica Count"),
        ("error_rate",     "Error Rate"),
    ]
    header = f"{'Metric':<25} {'Fixed Mean':>12} {'Fixed Std':>10} {'HPA Mean':>12} {'HPA Std':>10} {'Δ%':>8}"
    print("\n" + "=" * len(header))
    print("STATISTICAL SUMMARY")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    for key, label in metrics:
        _, vf = extract(fixed, key)
        _, vh = extract(hpa,   key)
        mf, sf = float(np.nanmean(vf)), float(np.nanstd(vf))
        mh, sh = float(np.nanmean(vh)), float(np.nanstd(vh))
        delta = (mh - mf) / mf * 100 if mf != 0 else 0
        sign = "+" if delta > 0 else ""
        print(f"{label:<25} {mf:>12.2f} {sf:>10.2f} {mh:>12.2f} {sh:>10.2f} {sign}{delta:>7.1f}%")
    print("=" * len(header))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Analyze HPA evaluation results")
    base_dir = Path(__file__).parent.parent / "sample_data"
    parser.add_argument("--fixed", default=str(base_dir / "fixed_metrics.csv"))
    parser.add_argument("--hpa",   default=str(base_dir / "hpa_metrics.csv"))
    args = parser.parse_args()

    for path in [args.fixed, args.hpa]:
        if not os.path.exists(path):
            print(f"ERROR: {path} not found. Run 'python3 analysis/simulate_results.py' first.", file=sys.stderr)
            sys.exit(1)

    fixed = load_csv(args.fixed)
    hpa   = load_csv(args.hpa)
    print(f"Loaded: {len(fixed)} fixed rows, {len(hpa)} HPA rows")

    out_dir = Path(args.fixed).parent / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Generating figures...")
    fig_latency(fixed, hpa, out_dir)
    fig_throughput(fixed, hpa, out_dir)
    fig_cpu_replicas(hpa, out_dir)
    fig_cost_performance(fixed, hpa, out_dir)

    print_summary(fixed, hpa)
    print(f"\nAll figures saved to {out_dir}/")


if __name__ == "__main__":
    main()
