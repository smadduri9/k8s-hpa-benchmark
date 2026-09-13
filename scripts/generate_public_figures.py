#!/usr/bin/env python3
"""Render canonical v1.1 SVGs and 2x PNGs from publication summaries only.

OS/arch assumptions: Python 3.14, macOS or Linux, pinned repository Matplotlib.
"""

import csv
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "artifacts/v1.1/summary"
OUTPUT = ROOT / "docs/assets/figures"
ARMS = ("fixed", "hpa_tuned", "hpa_stock")
LABELS = ("Fixed", "Tuned HPA", "Stock HPA")
COLORS = ("#586777", "#16757B", "#B46726")
HATCHES = ("", "///", "...")
SVG = "http://www.w3.org/2000/svg"


def read_csv(name):
    with (SUMMARY / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


def canvas(title, subtitle, ylabel, size=(8, 5)):
    fig, ax = plt.subplots(figsize=size, dpi=100)
    fig.subplots_adjust(left=.12, right=.97, top=.78, bottom=.17)
    fig.text(.12, .93, title, fontsize=17, fontweight="bold", color="#202C37")
    fig.text(.12, .865, subtitle, fontsize=10, color="#465460")
    ax.set_ylabel(ylabel)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#DFE4E8", linewidth=.7)
    ax.set_axisbelow(True)
    return fig, ax


def save(fig, name, title, description):
    path = OUTPUT / f"{name}.svg"
    fig.savefig(path, metadata={"Date": None, "Creator": "k8s-hpa-benchmark publication pipeline"})
    tree = ET.parse(path)
    root = tree.getroot()
    root.set("role", "img")
    root.set("aria-labelledby", f"{name}-title {name}-desc")
    heading = ET.Element(f"{{{SVG}}}title", {"id": f"{name}-title"})
    heading.text = title
    desc = ET.Element(f"{{{SVG}}}desc", {"id": f"{name}-desc"})
    desc.text = description
    root.insert(0, desc)
    root.insert(0, heading)
    ET.register_namespace("", SVG)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    fig.savefig(OUTPUT / f"{name}.png", dpi=200, metadata={"Software": "k8s-hpa-benchmark publication pipeline"})
    plt.close(fig)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "svg.fonttype": "none", "svg.hashsalt": "hpa-publication-v1.1",
                         "figure.facecolor": "white", "axes.facecolor": "white",
                         "savefig.facecolor": "white", "axes.labelcolor": "#202C37"})
    h = json.loads((SUMMARY / "headline.json").read_text())
    f, p = h["flash"], h["ready_pod_time"]
    names = ("fixed", "tuned", "stock")
    title = "Flash workload · client p95 latency"
    subtitle = f"Median across {f['repetitions']} paired repetitions · lower is faster"
    fig, ax = canvas(title, subtitle, "Client p95 latency (ms)")
    values = [f[f"{name}_p95_median_ms"] for name in names]
    for i, value in enumerate(values):
        ax.bar(i, value, width=.62, color=COLORS[i], hatch=HATCHES[i], edgecolor="#253541", linewidth=.7)
        ax.text(i, value + max(values)*.035, f"{value:g} ms", ha="center", fontweight="bold")
    ax.set_xticks(range(3), LABELS)
    ax.set_ylim(0, max(values)*1.22)
    save(fig, "latency_p95_medians", title, subtitle + "; " + "; ".join(f"{label}: {v:g} milliseconds" for label, v in zip(LABELS, values)))

    rows = read_csv("flash_repetitions.csv")
    reps = sorted({int(r["repetition"]) for r in rows})
    title = "Flash workload · every paired repetition"
    subtitle = f"Tuned lower than fixed: {f['tuned_better_than_fixed_reps']}/{f['repetitions']} · Stock lower than fixed: {f['stock_better_than_fixed_reps']}/{f['repetitions']}"
    fig, ax = canvas(title, subtitle, "Client p95 latency (ms)", (9, 5))
    all_values, descriptions = [], []
    for i, arm in enumerate(ARMS):
        values = [float(next(r["client_p95_ms"] for r in rows if r["arm"] == arm and int(r["repetition"]) == rep)) for rep in reps]
        all_values.extend(values)
        positions = [j + (i - 1)*.25 for j in range(len(reps))]
        ax.bar(positions, values, width=.23, label=LABELS[i], color=COLORS[i], hatch=HATCHES[i], edgecolor="#253541", linewidth=.6)
        for x, value in zip(positions, values):
            ax.text(x, value + 7, f"{value:g}", ha="center", fontsize=8)
        descriptions.append(f"{LABELS[i]}, repetitions in order: " + ", ".join(f"{v:g}" for v in values))
    ax.set_xticks(range(len(reps)), [f"Rep {r}" for r in reps])
    ax.set_ylim(0, max(all_values)*1.25)
    ax.legend(loc="upper center", bbox_to_anchor=(.5, 1.16), ncol=3, frameon=False, fontsize=10)
    save(fig, "latency_p95_by_rep", title, subtitle + ". " + "; ".join(descriptions))

    title = "Flash workload · ready-pod time"
    subtitle = "Sample-span integral of ready replicas · median by arm"
    fig, ax = canvas(title, subtitle, "Median ready-pod hours")
    values = [p[f"{name}_median_pod_hours"] for name in names]
    counts = [p[f"{name}_n"] for name in names]
    for i, (value, n) in enumerate(zip(values, counts)):
        ax.bar(i, value, width=.62, color=COLORS[i], hatch=HATCHES[i], edgecolor="#253541", linewidth=.7)
        ax.text(i, value + max(values)*.03, f"{value:.5f}\nn={n}", ha="center", fontsize=10)
    ax.set_xticks(range(3), LABELS)
    ax.set_ylim(0, max(values)*1.28)
    fig.text(.12, .045, "Tuned rep 1 excluded: contaminated sampler. Its latency measurement is retained.", fontsize=9)
    save(fig, "ready_pod_hours", title, subtitle + ". Tuned rep 1 excluded because its sampler is contaminated. " + "; ".join(f"{label}: {value:.10f} ready-pod hours, n={n}" for label, value, n in zip(LABELS, values, counts)))

    title = "Observed scale-out across five workloads"
    subtitle = "Each marker is one arm’s in-window peak desired replicas; repeated values are offset"
    fig, ax = canvas(title, subtitle, "", (10, 5.5))
    fig.subplots_adjust(left=.25, bottom=.22)
    ax.grid(False)
    ax.grid(axis="x", color="#DFE4E8", linewidth=.7)
    order = ("wc98_constant", "wc98_periodic", "rr_periodic", "wc98_ramp", "wc98_flash")
    labels = ("Constant", "WC98 periodic", "RetailRocket periodic", "Ramp", "Flash")
    floor = h["replica_scaling"]["floor_replicas"]
    ax.axvline(floor, color="#586777", linestyle="--", linewidth=1.3, label=f"Minimum = {floor}")
    descriptions = []
    highest = floor
    for j, (shape, label) in enumerate(zip(order, labels)):
        workload = h["replica_scaling"]["workloads"][shape]
        values = workload["hpa_tuned"] + workload["hpa_stock"]
        highest = max(highest, *values)
        ax.hlines(j, min(values), max(values), color="#AAB4BC", linewidth=2)
        for i, (arm, marker) in enumerate((("hpa_tuned", "o"), ("hpa_stock", "D"))):
            arm_values = workload[arm]
            ys = [j + (i*2-1)*.2 + (arm_values[:k].count(value)-(arm_values.count(value)-1)/2)*.09
                  for k, value in enumerate(arm_values)]
            ax.scatter(arm_values, ys, color=COLORS[i+1], marker=marker, s=45,
                       edgecolors="#253541", linewidths=.5, label=LABELS[i+1] if j == 0 else None, zorder=3)
        descriptions.append(f"{label}: tuned {workload['hpa_tuned']}, stock {workload['hpa_stock']}")
    ax.set_yticks(range(len(order)), [f"{label} (n={len(h['replica_scaling']['workloads'][shape]['repetitions'])})" for label, shape in zip(labels, order)])
    ax.invert_yaxis()
    ax.set_xticks(range(floor, highest+1))
    ax.set_xlim(floor-.65, highest+.65)
    ax.set_xlabel("Peak spec_replicas in the anchored measurement window")
    ax.legend(loc="upper center", bbox_to_anchor=(.45, -.22), ncol=3, frameon=False, fontsize=10)
    save(fig, "replica_scaling", title, f"Minimum {floor}. " + "; ".join(descriptions))
    files = sorted(p for p in OUTPUT.iterdir() if p.suffix in {".svg", ".png"})
    inputs = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(SUMMARY.iterdir()) if p.is_file()}
    inputs["scripts/generate_public_figures.py"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    manifest = {"schema_version": 1, "hash_scope": "Images; SHA256SUMS also hashes this manifest",
                "artifacts": [{"path": p.name, "bytes": p.stat().st_size,
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "kind": "derived",
                    "source_or_derivation": "scripts/generate_public_figures.py from canonical summaries",
                    "sources": inputs, "units": "ready-pod hours" if p.stem == "ready_pod_hours"
                    else "replica counts" if p.stem == "replica_scaling" else "milliseconds",
                    "exclusions": "Inherited from artifacts/v1.1/exclusions.csv via summaries"}
                    for p in files]}
    manifest_path = OUTPUT / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    files.append(manifest_path)
    checksums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files)
    (OUTPUT / "SHA256SUMS").write_text(checksums)
    print(f"PUBLIC_FIGURES_PASS figures={len(files)-1}")


if __name__ == "__main__":
    main()
