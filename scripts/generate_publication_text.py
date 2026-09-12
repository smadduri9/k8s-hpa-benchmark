#!/usr/bin/env python3
"""Update bounded publication blocks from v1.1 summaries; --check is read-only.

OS/arch assumptions: Python 3.14, macOS or Linux; standard library only.
"""

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "artifacts/v1.1"
BEGIN = "<!-- BEGIN V1.1 GENERATED FINDINGS -->"
END = "<!-- END V1.1 GENERATED FINDINGS -->"
ARMS = ("fixed", "hpa_tuned", "hpa_stock")


def rows(name):
    with (BUNDLE / "summary" / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


def render(prefix, detailed=False):
    h = json.loads((BUNDLE / "summary/headline.json").read_text())
    f, t, scaling = h["flash"], h["ready_pod_time"], h["replica_scaling"]
    peaks = rows("replica_peaks.csv")
    ramp = [r for r in peaks if r["shape"] == "wc98_ramp" and r["arm"] == "hpa_tuned"]
    link = lambda text, path: f"[{text}]({prefix}{path})"
    lines = [BEGIN, "", f"Evidence and offline commands: {link('v1.1 package', 'README.md')}. "
             f"Authority: {link('verify.py', 'verify.py')} and {link('headline.json', 'summary/headline.json')}; "
             "the historical general aggregator does not apply the publication exclusions.", "",
             f"Trace-derived WorldCup98 and RetailRocket load; three arms (`fixed`, `hpa_tuned`, `hpa_stock`). "
             f"`SHAPE_MEAN_USERS={h['capacity']['shape_mean_users']}` is a model-derived calibration parameter, "
             f"not measured maximum capacity. Declared HPA floor: {scaling['floor_replicas']} replicas.", "",
             "### Finding 1. Flash latency and ready-pod time", "",
             f"Client p95 medians: fixed **{f['fixed_p95_median_ms']:g} ms**, tuned **{f['tuned_p95_median_ms']:g} ms**, "
             f"stock **{f['stock_p95_median_ms']:g} ms**, across **{f['repetitions']} paired repetitions**. "
             f"Tuned p95 was lower than fixed in {f['tuned_better_than_fixed_reps']}/{f['repetitions']}; "
             f"stock was lower in {f['stock_better_than_fixed_reps']}/{f['repetitions']}.", "",
             f"Exact two-sided Wilcoxon: fixed/tuned **p={f['tuned_vs_fixed_wilcoxon_p']:.6f}**, "
             f"fixed/stock **p={f['stock_vs_fixed_wilcoxon_p']:.6f}**, "
             f"tuned/stock **p={f['tuned_vs_stock_wilcoxon_p']:.6f}**. "
             "These are unadjusted tests. The tuned/stock comparison does not establish equivalence or a benefit from tuning. "
             "Arm order was not randomized.", "",
             f"Median ready-pod hours: fixed **{t['fixed_median_pod_hours']:.5f} (n={t['fixed_n']})**, "
             f"tuned **{t['tuned_median_pod_hours']:.5f} (n={t['tuned_n']})**, "
             f"stock **{t['stock_median_pod_hours']:.5f} (n={t['stock_n']})**. "
             f"Ratios of unrounded medians are **+{t['tuned_vs_fixed_ratio_of_medians_pct']:.6f}%** tuned/fixed and "
             f"**+{t['stock_vs_fixed_ratio_of_medians_pct']:.6f}%** stock/fixed. "
             "Ready-pod time measures ready replicas integrated over sampled time; it does not measure consumed CPU or billing.", "",
             f"{link('Exclusions policy', 'exclusions.csv')}: tuned flash rep-1 retains its latency measurement but is excluded "
             "from ready-pod time because its replica series spans beyond the benchmark window. "
             "The contaminated file is preserved unchanged; its historical process origin is not independently evidenced. "
             f"See {link('per-repetition results', 'summary/flash_repetitions.csv')} and "
             f"{link('paired tests', 'summary/statistical_tests.csv')}.", "",
             "### Finding 2. Workload coverage", "",
             "Observed in-window peak `spec_replicas`, listed in repetition order:", "",
             "| Workload | Repetitions | Tuned HPA | Stock HPA |", "|---|---:|---|---|"]
    for shape in ("wc98_flash", "wc98_ramp", "wc98_constant", "wc98_periodic", "rr_periodic"):
        data = scaling["workloads"][shape]
        lines.append(f"| `{shape}` | {len(data['repetitions'])} | {', '.join(map(str, data['hpa_tuned']))} | {', '.join(map(str, data['hpa_stock']))} |")
    lines.extend(["", f"Fixed-arm peaks were {scaling['floor_replicas']} throughout this completed scope. "
                  "Flash, ramp and both periodic workloads showed scale-out above the floor in at least one retained arm/repetition. "
                  "Constant did not. These observations do not establish peak-to-mean ratio as a sufficient predictor of HPA engagement. "
                  f"Source: {link('replica peaks and request counts', 'summary/replica_peaks.csv')}.", "",
                  "### Finding 3. Ramp run/deployment sensitivity", "",
                  f"Two ramp repetitions produced similar measured request counts "
                  f"({int(ramp[0]['requests']):,} and {int(ramp[1]['requests']):,} in the tuned arm) but different scale-out: "
                  f"tuned peaked at {ramp[0]['peak_spec_replicas']} replicas in one repetition and {ramp[1]['peak_spec_replicas']} in the other. "
                  "The retained evidence does not establish a hardware-level cause. This is evidence of run/deployment sensitivity, "
                  "not a causal CPU-platform finding. Paired warm-up CPU vectors and hardware inventories are `MISSING`.", "",
                  "Flash has six completed repetitions, ramp two, and each other workload one. The originally defined "
                  "three-repetition scope for the non-flash workloads was not completed. Incomplete ramp rep-3 is excluded. "
                  "The non-flash observations are descriptive, not a workload ranking."])
    if detailed:
        lines.extend(["", "### Flash per-repetition client p95 (ms)", "",
                      "| Rep | Fixed | Tuned HPA | Stock HPA |", "|---:|---:|---:|---:|"])
        flash = rows("flash_repetitions.csv")
        for rep in sorted({int(r["repetition"]) for r in flash}):
            values = [next(float(r["client_p95_ms"]) for r in flash if int(r["repetition"]) == rep and r["arm"] == arm) for arm in ARMS]
            lines.append(f"| {rep} | " + " | ".join(f"{v:g}" for v in values) + " |")
        requests = [int(r["requests"]) for r in flash]
        lines.extend(["", f"Flash measured request counts span {min(requests):,}–{max(requests):,} across its completed arms. "
                      "Endpoint and Aggregated rows remain available in each arm’s raw stats CSV."])
    return "\n".join([*lines, "", END])


def render_overview(section):
    h = json.loads((BUNDLE / "summary/headline.json").read_text())
    f, t = h["flash"], h["ready_pod_time"]
    if section == "FINDINGS":
        lines = [f"**Flash workload · {f['repetitions']} paired repetitions · median client p95**", "",
                 "| Fixed capacity | Tuned HPA | Stock HPA |", "|---:|---:|---:|",
                 f"| **{f['fixed_p95_median_ms']:g} ms** | **{f['tuned_p95_median_ms']:g} ms** | **{f['stock_p95_median_ms']:g} ms** |", "",
                 f"Tuned HPA beat fixed in **{f['tuned_better_than_fixed_reps']}/{f['repetitions']}** repetitions. "
                 f"Stock HPA beat fixed in **{f['stock_better_than_fixed_reps']}/{f['repetitions']}** repetitions."]
    elif section == "RESOURCES":
        lines = ["| Policy | Median ready-pod time | Repetitions | Ratio of medians vs fixed |",
                 "|---|---:|---:|---:|",
                 f"| Fixed | {t['fixed_median_pod_hours']:.5f} pod-hours | {t['fixed_n']} | Reference |",
                 f"| Tuned HPA | {t['tuned_median_pod_hours']:.5f} pod-hours | {t['tuned_n']} | +{t['tuned_vs_fixed_ratio_of_medians_pct']:.3f}% |",
                 f"| Stock HPA | {t['stock_median_pod_hours']:.5f} pod-hours | {t['stock_n']} | +{t['stock_vs_fixed_ratio_of_medians_pct']:.3f}% |"]
    elif section == "TESTS":
        lines = ["| Flash comparison | Exact two-sided Wilcoxon p |", "|---|---:|",
                 f"| Fixed vs tuned HPA | {f['tuned_vs_fixed_wilcoxon_p']:g} |",
                 f"| Fixed vs stock HPA | {f['stock_vs_fixed_wilcoxon_p']:g} |",
                 f"| Tuned vs stock HPA | {f['tuned_vs_stock_wilcoxon_p']:g} |"]
    elif section == "SCALING":
        lines = ["| Workload | Observed HPA peak replicas | Repetitions |", "|---|---:|---:|"]
        for shape, label in (("wc98_constant", "Constant"), ("wc98_periodic", "WC98 periodic"),
                             ("rr_periodic", "RetailRocket periodic"), ("wc98_ramp", "Ramp"),
                             ("wc98_flash", "Flash")):
            data = h["replica_scaling"]["workloads"][shape]
            values = data["hpa_tuned"] + data["hpa_stock"]
            low, high = min(values), max(values)
            peak = str(low) if low == high else f"{low}–{high}"
            lines.append(f"| {label} | {peak} | {len(data['repetitions'])} |")
    else:
        raise ValueError(f"UNKNOWN_OVERVIEW_SECTION {section}")
    return "\n".join([f"<!-- BEGIN V1.1 GENERATED {section} -->", "", *lines, "",
                      f"<!-- END V1.1 GENERATED {section} -->"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for filename, prefix, detailed in (
        ("RESULTS.md", "artifacts/v1.1/", True),
        ("docs/index.md", "https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/artifacts/v1.1/", False),
    ):
        path = ROOT / filename
        text = path.read_text()
        if text.count(BEGIN) != 1 or text.count(END) != 1:
            raise ValueError(f"PUBLICATION_MARKERS_INVALID {filename}")
        before, rest = text.split(BEGIN)
        _, after = rest.split(END)
        expected = before + render(prefix, detailed) + after
        if args.check:
            if expected != text:
                raise ValueError(f"PUBLICATION_TEXT_MISMATCH {filename}")
        else:
            path.write_text(expected)
    path = ROOT / "README.md"
    text = path.read_text()
    expected = text
    for section in ("FINDINGS", "SCALING", "RESOURCES", "TESTS"):
        begin = f"<!-- BEGIN V1.1 GENERATED {section} -->"
        end = f"<!-- END V1.1 GENERATED {section} -->"
        if expected.count(begin) != 1 or expected.count(end) != 1:
            raise ValueError(f"PUBLICATION_MARKERS_INVALID README.md {section}")
        before, rest = expected.split(begin)
        _, after = rest.split(end)
        expected = before + render_overview(section) + after
    if args.check:
        if expected != text:
            raise ValueError("PUBLICATION_TEXT_MISMATCH README.md")
    else:
        path.write_text(expected)
    print("PUBLICATION_TEXT_PASS documents=3")


if __name__ == "__main__":
    main()
