#!/usr/bin/env python3
"""Recompute and verify the v1.1 publication using this directory only.

OS/arch assumptions: Python 3.14 on macOS or Linux; standard library only.
Default is read-only. --write regenerates summaries and their integrity inventory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from statistics import median

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
ARMS = ("fixed", "hpa_tuned", "hpa_stock")
MISSING = "MISSING"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_text(value):
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"


def rows(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def csv_text(records):
    require(bool(records), "EMPTY_SUMMARY")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(records[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue()


def epoch(value):
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(timestamp.tzinfo is not None, "TIMESTAMP_WITHOUT_TIMEZONE")
    return timestamp.timestamp()


def number(value):
    result = float(value)
    require(math.isfinite(result), "NONFINITE_MEASUREMENT")
    return result


def wilcoxon(a, b):
    """Exact sign-rank distribution, midranks for ties, zero differences removed."""
    require(len(a) == len(b), "UNPAIRED_SAMPLES")
    differences = [number(y) - number(x) for x, y in zip(a, b) if y != x]
    n = len(differences)
    require(n <= 50, "EXACT_TEST_TOO_LARGE")
    absolute = sorted(abs(d) for d in differences)
    ranks2 = [sum(i + 1 for i, v in enumerate(absolute) if v == abs(d)) * 2
              // absolute.count(abs(d)) for d in differences]
    positive = sum(r for r, d in zip(ranks2, differences) if d > 0)
    statistic2 = min(positive, sum(ranks2) - positive)
    distribution = {0: 1}
    for rank in ranks2:
        updated = dict(distribution)
        for total, count in distribution.items():
            updated[total + rank] = updated.get(total + rank, 0) + count
        distribution = updated
    p = min(1.0, 2 * sum(c for total, c in distribution.items() if total <= statistic2) / 2**n)
    return {"n_pairs": len(a), "n_effective": n, "zero_differences": len(a) - n,
            "statistic": statistic2 / 2, "p_two_sided": p,
            "p_floor": min(1.0, 2 / 2**n), "method": "exact_signed_rank"}


def policy(root):
    records = rows(root / "exclusions.csv")
    decisions = {}
    for row in records:
        key = (row["shape"], int(row["repetition"]), row["arm"], row["metric"])
        require(key not in decisions, "DUPLICATE_EXCLUSION")
        require(row["action"] in {"KEEP", "EXCLUDE"} and row["reason"], "INVALID_EXCLUSION")
        decisions[key] = row["action"]
    return records, decisions


def excluded(decisions, shape, rep, arm, metric):
    for key in [(shape, rep, "*", "all"), (shape, rep, arm, "all"),
                (shape, rep, arm, metric)]:
        if key in decisions:
            return decisions[key] == "EXCLUDE"
    return False


def replica_measurements(path, start, duration, exclude_time):
    samples = rows(path)
    times = [epoch(row["timestamp"]) for row in samples]
    require(len(samples) >= 2 and all(b > a for a, b in zip(times, times[1:])),
            "INVALID_REPLICA_TIMESTAMPS")
    for row in samples:
        for column in ("spec_replicas", "status_replicas", "ready_replicas"):
            require(int(row[column]) >= 0, "INVALID_REPLICA_COUNT")
    window = [row for row, t in zip(samples, times) if start <= t <= start + duration]
    require(len(window) >= 2, "EMPTY_ANCHORED_REPLICA_WINDOW")
    if not exclude_time:
        require(all(start <= t <= start + duration for t in times), "UNEXCLUDED_SERIES_OUTSIDE_WINDOW")
        require(times[0] - start <= 30 and start + duration - times[-1] <= 30,
                "INCOMPLETE_REPLICA_WINDOW")
        require(max(b - a for a, b in zip(times, times[1:])) <= 45, "REPLICA_SAMPLING_GAP")
    hours = MISSING if exclude_time else sum(
        int(row["ready_replicas"]) * (b - a)
        for row, a, b in zip(samples, times, times[1:])
    ) / 3600
    return {"peak_spec_replicas": max(int(r["spec_replicas"]) for r in window),
            "peak_ready_replicas": max(int(r["ready_replicas"]) for r in window),
            "samples_total": len(samples), "samples_in_window": len(window),
            "series_span_seconds": times[-1] - times[0], "ready_pod_hours": hours}


def calibration(root):
    steps = rows(root / "capacity/steps.csv")
    samples = rows(root / "capacity/cpu_samples.csv")
    require(len(samples) == len(steps), "PROBE_CPU_SAMPLE_COUNT")
    for step, sample in zip(steps, samples):
        require(step["step"] == sample["step"] and
                number(step["median_millicores"]) == number(sample["median_millicores"]),
                "PROBE_CPU_SAMPLE_MISMATCH")
    require([int(r["step"]) for r in steps] == list(range(1, len(steps) + 1)), "PROBE_STEP_SEQUENCE")
    for row in steps:
        stats = rows(root / f"capacity/step-{row['step']}/locust_stats.csv")
        aggregated = [r for r in stats if r["Name"] == "Aggregated"]
        require(len(aggregated) == 1, "PROBE_AGGREGATED_ROW")
        require(number(row["rps"]) == number(aggregated[0]["Requests/s"]), "PROBE_RPS_MISMATCH")
    stop = next((i for i, r in enumerate(steps) if r["stop_trigger"] != "none"), None)
    require(stop is not None and stop > 0 and stop == len(steps) - 1, "INVALID_PROBE_STOP")
    pre = steps[stop - 1]
    constant = json.loads((root / "workloads/provenance/wc98_constant.json").read_text())
    flash = json.loads((root / "workloads/provenance/wc98_flash.json").read_text())
    settings = json.loads((root / "metadata/runs.json").read_text())["declared_config"]
    floor = settings["floor_replicas"]
    require(int(pre["ready_pods"]) == floor, "PROBE_FLOOR_MISMATCH")
    require(steps[stop]["stop_trigger"] == "cpu_saturation" and
            number(steps[stop]["median_millicores"]) >= 800, "PROBE_STOP_MISMATCH")
    users, rps = int(pre["users"]), number(pre["rps"])
    cpu = number(pre["median_millicores"]) / 1000 * floor
    target = floor * settings["pod_cpu_request_cores"] * settings["hpa_cpu_target_fraction"]
    constant_min = min(constant["unit_mean_plateaus"])
    flash_peak = flash["peak_to_mean"]
    feasible = [u for u in range(1, 5001)
                if round(constant_min * u) * (rps / users) * (cpu / rps) < target
                < round(flash_peak * u) * (rps / users) * (cpu / rps)]
    require(bool(feasible), "NO_FEASIBLE_CALIBRATION")
    require(all(r["shape_mean_users"] == max(feasible) for r in
                json.loads((root / "metadata/runs.json").read_text())["runs"]),
            "RUN_CALIBRATION_MISMATCH")
    result = {"SHAPE_MEAN_USERS": max(feasible), "feasible_U": feasible,
              "pre_saturation_step": int(pre["step"]), "stopping_step": int(steps[stop]["step"]),
              "stop_trigger": steps[stop]["stop_trigger"],
              "rps_per_user": rps / users, "cpu_per_rps": cpu / rps,
              "target_cluster_cores": target, "constant_min_unit": constant_min,
              "flash_peak_to_mean": flash_peak,
              "interpretation": "model-derived calibration parameter; not measured maximum capacity",
              "individual_pod_cpu_observations": MISSING}
    require(result == json.loads((root / "capacity/derivation.json").read_text()), "CALIBRATION_MISMATCH")
    return result


def verify_workloads(root, shapes):
    for shape in shapes:
        provenance = json.loads((root / f"workloads/provenance/{shape}.json").read_text())
        window = rows(root / f"workloads/{shape}_1s.csv")
        size = provenance["source_window_sec"]
        require(len(window) == size, "WORKLOAD_WINDOW_LENGTH")
        require([int(r["second_offset"]) for r in window] == list(range(size)), "WORKLOAD_WINDOW_GAP")
        counts = [int(r["request_count"]) for r in window]
        require(min(counts) >= 0, "NEGATIVE_WORKLOAD_COUNT")
        width = size // 36
        bins = [sum(counts[i:i + width]) for i in range(0, size, width)]
        mean = sum(bins) / len(bins)
        require(mean > 0, "EMPTY_WORKLOAD")
        unit = [round(v / mean, 8) for v in bins]
        require(unit == provenance["unit_mean_plateaus"], f"WORKLOAD_PLATEAUS_MISMATCH {shape}")
        require(round(max(bins) / mean, 6) == provenance["peak_to_mean"], "WORKLOAD_PEAK_MISMATCH")


def compute(root):
    _, decisions = policy(root)
    metadata = json.loads((root / "metadata/runs.json").read_text())
    completion = json.loads((root / "metadata/completion.json").read_text())
    complete = {(r["shape"], r["repetition"], r["arm"]): r for r in completion}
    require(len(complete) == len(completion), "DUPLICATE_COMPLETION")
    floor = metadata["declared_config"]["floor_replicas"]
    records, peaks, seen = [], [], set()
    for run in metadata["runs"]:
        shape, duration = run["shape"], run["duration_seconds"]
        require(re.fullmatch(r"[a-z][a-z0-9_]*", shape) is not None, "INVALID_SHAPE_PATH")
        require((root / "runs" / shape / "STATUS").read_text().splitlines()[0].startswith("COMPLETE"),
                "RUN_NOT_COMPLETE")
        expected = {(shape, rep, arm) for rep in run["completed_repetitions"] for arm in ARMS}
        observed = {(shape, int(p.parent.name.removeprefix("rep-")), p.name)
                    for p in (root / "runs" / shape).glob("rep-*/*") if p.is_dir()}
        require(observed == expected, f"ARM_INVENTORY_MISMATCH {shape}")
        for key in sorted(expected):
            _, rep, arm = key
            require(not excluded(decisions, shape, rep, arm, "all"), "EXCLUDED_REPETITION_PUBLISHED")
            directory = root / f"runs/{shape}/rep-{rep}/{arm}"
            require((directory / "STATUS").read_text().splitlines()[0] == "PASS", "ARM_NOT_PASS")
            require(key in complete and complete[key]["marker"] == "Shape test stopping", "SHAPE_INCOMPLETE")
            start = epoch((directory / "t0.txt").read_text().strip())
            require(start == epoch(complete[key]["t0"]), "COMPLETION_T0_MISMATCH")
            stats = rows(directory / f"locust_{arm}_stats.csv")
            aggregate = [r for r in stats if r["Name"] == "Aggregated"]
            require(len(aggregate) == 1, "AGGREGATED_ROW_COUNT")
            aggregated = aggregate[0]
            require(number(aggregated["95%"]) >= 0, "NEGATIVE_LATENCY")
            requests, failures = int(aggregated["Request Count"]), int(aggregated["Failure Count"])
            require(requests > 0 and 0 <= failures <= requests, "INVALID_LOCUST_COUNTS")
            for field in ("Request Count", "Failure Count"):
                require(sum(int(r[field]) for r in stats if r["Name"] != "Aggregated") == int(aggregated[field]),
                        "LOCUST_ENDPOINT_COUNT_MISMATCH")
            mode = "fixed" if arm == "fixed" else "hpa"
            exclude_time = excluded(decisions, shape, rep, arm, "ready_pod_hours")
            series = replica_measurements(directory / f"replica_series_{mode}.csv", start, duration, exclude_time)
            require(series["peak_spec_replicas"] >= floor, "REPLICA_BELOW_FLOOR")
            record = {"shape": shape, "repetition": rep, "arm": arm,
                      "client_p95_ms": MISSING if excluded(decisions, shape, rep, arm, "latency") else number(aggregated["95%"]),
                      "requests": requests, "failures": failures, **series}
            records.append(record)
            peaks.append({k: record[k] for k in ("shape", "repetition", "arm", "requests", "failures",
                          "peak_spec_replicas", "peak_ready_replicas", "samples_total", "samples_in_window")})
            seen.add(key)
    require(set(complete) == seen, "COMPLETION_INVENTORY_MISMATCH")
    for (shape, rep, arm, metric), action in decisions.items():
        require(metric in {"all", "latency", "ready_pod_hours"}, "UNKNOWN_EXCLUSION_METRIC")
        if metric != "all":
            require((shape, rep, arm) in seen, "EXCLUSION_WITHOUT_EVIDENCE")
        elif action == "EXCLUDE":
            require(not any(s == shape and r == rep and (arm == "*" or a == arm) for s, r, a in seen),
                    "EXCLUDED_REPETITION_PRESENT")
    flash = [r for r in records if r["shape"] == "wc98_flash"]
    by_arm = {arm: sorted([r for r in flash if r["arm"] == arm], key=lambda r: r["repetition"]) for arm in ARMS}
    latency, pod_time, tests = [], [], []
    for arm, arm_rows in by_arm.items():
        values = [r["client_p95_ms"] for r in arm_rows if r["client_p95_ms"] != MISSING]
        require(len(values) == len(arm_rows), "FLASH_LATENCY_EXCLUDED")
        latency.append({"arm": arm, "client_p95_median_ms": median(values), "n": len(values)})
        time = [r["ready_pod_hours"] for r in arm_rows if r["ready_pod_hours"] != MISSING]
        pod_time.append({"arm": arm, "median_ready_pod_hours": median(time), "n": len(time)})
    for a, b in (("fixed", "hpa_tuned"), ("fixed", "hpa_stock"), ("hpa_tuned", "hpa_stock")):
        for metric in ("client_p95_ms", "ready_pod_hours"):
            pairs = [(x[metric], y[metric]) for x, y in zip(by_arm[a], by_arm[b])
                     if x[metric] != MISSING and y[metric] != MISSING]
            require(bool(pairs), "NO_STATISTICAL_PAIRS")
            tests.append({"metric": metric, "arm_a": a, "arm_b": b,
                          **wilcoxon([x for x, _ in pairs], [y for _, y in pairs])})
    time = {r["arm"]: r for r in pod_time}
    lat = {r["arm"]: r for r in latency}
    n = len(by_arm["fixed"])
    test_p = {(r["arm_a"], r["arm_b"]): r["p_two_sided"] for r in tests if r["metric"] == "client_p95_ms"}
    headline = {"flash": {"repetitions": n}, "ready_pod_time": {},
                "replica_scaling": {"floor_replicas": floor, "workloads": {}},
                "capacity": {"shape_mean_users": calibration(root)["SHAPE_MEAN_USERS"],
                             "basis": "model-derived calibration parameter"}}
    for arm, name in zip(ARMS, ("fixed", "tuned", "stock")):
        headline["flash"][f"{name}_p95_median_ms"] = lat[arm]["client_p95_median_ms"]
        headline["ready_pod_time"][f"{name}_median_pod_hours"] = time[arm]["median_ready_pod_hours"]
        headline["ready_pod_time"][f"{name}_n"] = time[arm]["n"]
        if arm != "fixed":
            headline["flash"][f"{name}_vs_fixed_wilcoxon_p"] = test_p[("fixed", arm)]
            headline["flash"][f"{name}_better_than_fixed_reps"] = sum(
                x["client_p95_ms"] < y["client_p95_ms"] for x, y in zip(by_arm[arm], by_arm["fixed"]))
            headline["ready_pod_time"][f"{name}_vs_fixed_ratio_of_medians_pct"] = (
                time[arm]["median_ready_pod_hours"] / time["fixed"]["median_ready_pod_hours"] - 1) * 100
    headline["flash"]["tuned_vs_stock_wilcoxon_p"] = test_p[("hpa_tuned", "hpa_stock")]
    for run in metadata["runs"]:
        shape = run["shape"]
        headline["replica_scaling"]["workloads"][shape] = {
            "repetitions": run["completed_repetitions"],
            **{arm: [r["peak_spec_replicas"] for r in records if r["shape"] == shape and r["arm"] == arm] for arm in ARMS}}
    verify_workloads(root, [r["shape"] for r in metadata["runs"]])
    return {"summary/flash_repetitions.csv": csv_text(flash),
            "summary/latency.csv": csv_text(latency), "summary/pod_hours.csv": csv_text(pod_time),
            "summary/statistical_tests.csv": csv_text(tests), "summary/replica_peaks.csv": csv_text(peaks),
            "summary/headline.json": json_text(headline)}


def public_files(root):
    files = []
    for path in root.rglob("*"):
        require(not path.is_symlink(), "SYMLINK_NOT_ALLOWED")
        if path.is_file() and "__pycache__" not in path.parts:
            files.append(path)
    return sorted(files)


def inventory(root):
    sources = json.loads((root / "metadata/sources.json").read_text())
    exclusions = rows(root / "exclusions.csv")
    entries = []
    for path in public_files(root):
        relative = path.relative_to(root).as_posix()
        if relative in {"MANIFEST.json", "SHA256SUMS"}:
            continue
        origin = sources.get(relative, {"kind": "derived", "source_or_derivation":
                  "verify.py compute() from bundled evidence and exclusions.csv" if relative.startswith("summary/")
                  else "Publication documentation, policy or verification implementation"})
        entries.append({"path": relative, "bytes": path.stat().st_size, "sha256": digest(path.read_bytes()),
                        **origin, "units": origin.get("units", "not_applicable"),
                        "exclusions": exclusions if relative.startswith(("summary/", "runs/wc98_flash/rep-1/hpa_tuned/")) else []})
    return {"schema_version": 1, "hash_scope": "All bundle files except MANIFEST.json and SHA256SUMS; SHA256SUMS also hashes MANIFEST.json",
            "artifacts": entries}


def checksum_text(root, manifest):
    records = {e["path"]: e["sha256"] for e in manifest["artifacts"]}
    records["MANIFEST.json"] = digest((root / "MANIFEST.json").read_bytes())
    return "".join(f"{sha}  {path}\n" for path, sha in sorted(records.items()))


def security_scan(root):
    patterns = {
        "local_path": r"/(?:Users|home|private/var|var/folders)/[^\s]+",
        "email": r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
        "service_ip": r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])",
        "private_registry": r"[\w.-]+\.pkg\.dev|[\w.-]+\.internal",
        "project_identity": r"projects/[a-z][a-z0-9-]+|(?i:project_id)[\"\s:=]+[a-z][a-z0-9-]{5,}",
        "token": r"AIza[\w-]{30,}|gh[pousr]_[\w]{20,}|ya29\.[\w-]{15,}|AKIA[A-Z0-9]{16}",
        "private_key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        "bearer": r"(?i)bearer\s+[\w.~+/-]{12,}",
        "identity": r"\b[a-z][a-z0-9-]+-(?:fixed|hpa)-[a-z0-9]+-[a-z0-9]+|[\w-]+/(?:INFO|ERROR|WARNING)/locust",
        "visitor_identifiers": "(?i)" + "|".join(name + "id" for name in ("visitor", "transaction", "client", "object")),
    }
    problems = []
    for path in public_files(root):
        text = path.read_text(encoding="utf-8")
        for category, pattern in patterns.items():
            if re.search(pattern, text):
                problems.append(f"{path.relative_to(root)}:{category}")
    require(not problems, "SECURITY_SCAN_FAILED " + ", ".join(problems))


def verify(root=ROOT, write=False):
    public_files(root)  # Reject links before opening any evidence through them.
    computed = compute(root)
    security_scan(root)
    if write:
        for relative, text in computed.items():
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8")
        (root / "MANIFEST.json").write_text(json_text(inventory(root)), encoding="utf-8")
        manifest = json.loads((root / "MANIFEST.json").read_text())
        (root / "SHA256SUMS").write_text(checksum_text(root, manifest), encoding="utf-8")
    for relative, text in computed.items():
        require((root / relative).read_text() == text, f"SUMMARY_MISMATCH {relative}")
    manifest = json.loads((root / "MANIFEST.json").read_text())
    require(manifest == inventory(root), "MANIFEST_MISMATCH")
    require((root / "SHA256SUMS").read_text() == checksum_text(root, manifest), "CHECKSUM_MISMATCH")
    return json.loads(computed["summary/headline.json"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="regenerate summaries, manifest and checksums")
    args = parser.parse_args()
    try:
        h = verify(write=args.write)
        f, t = h["flash"], h["ready_pod_time"]
        print(f"PASS flash p95 ms: fixed={f['fixed_p95_median_ms']:g} tuned={f['tuned_p95_median_ms']:g} stock={f['stock_p95_median_ms']:g}; n={f['repetitions']}")
        print(f"PASS exact Wilcoxon: {f['tuned_vs_fixed_wilcoxon_p']:.6f}, {f['stock_vs_fixed_wilcoxon_p']:.6f}, {f['tuned_vs_stock_wilcoxon_p']:.6f}")
        print(f"PASS ready-pod hours: {t['fixed_median_pod_hours']:.10f} (n={t['fixed_n']}), {t['tuned_median_pod_hours']:.10f} (n={t['tuned_n']}), {t['stock_median_pod_hours']:.10f} (n={t['stock_n']})")
        print(f"PASS ratios of unrounded medians: +{t['tuned_vs_fixed_ratio_of_medians_pct']:.6f}%, +{t['stock_vs_fixed_ratio_of_medians_pct']:.6f}%")
        print("PASS 33 arms; anchored replica peaks; 5 workload windows; calibration=69; exclusions; summaries; checksums; security")
    except (ValueError, KeyError, OSError, StopIteration) as error:
        print(f"PUBLICATION_VERIFY_FAILED {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
