#!/usr/bin/env python3
"""Curate v1.1 from retained local evidence; never modify the input artifacts.

OS/arch assumptions: macOS or Linux, Python 3.14 with repository requirements.
This is the local curation step. Public verification needs only artifacts/v1.1.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "artifacts/v1.1"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import build_shape_artifacts as shapes


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    origins = {}

    def write(relative, data, sources, kind="derived", units="not_applicable", operation="copy bytes"):
        path = OUTPUT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        origins[relative] = {"kind": kind, "source_or_derivation": operation,
                             "sources": [{"local_source": str(p.relative_to(REPO_ROOT)), "sha256": sha(p)} for p in sources],
                             "units": units}

    def copy(relative, source, units="not_applicable"):
        write(relative, source.read_bytes(), [source], "raw", units)

    def write_json(relative, obj, sources, operation):
        write(relative, (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode(), sources, operation=operation)

    completions, runs = [], []
    for shape in [spec["shape_name"] for spec in shapes.SHAPE_SPECS]:
        source = REPO_ROOT / "results/runs" / f"run-phase5-{shape}"
        manifest_path = source / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        completed_reps = []
        for rep in sorted(source.glob("rep-*"), key=lambda p: int(p.name[4:])):
            arms = [rep / name for name in ("fixed", "hpa_tuned", "hpa_stock")]
            if not all((a / "STATUS").exists() and (a / "STATUS").read_text().splitlines()[0] == "PASS" for a in arms):
                continue
            completed_reps.append(int(rep.name[4:]))
            for arm in arms:
                mode = "fixed" if arm.name == "fixed" else "hpa"
                target = f"runs/{shape}/{rep.name}/{arm.name}"
                for name, units in [(f"locust_{arm.name}_stats.csv", "milliseconds; requests; requests/second; bytes"),
                                    (f"replica_series_{mode}.csv", "UTC timestamps; replica counts"),
                                    ("t0.txt", "UTC timestamp"), ("STATUS", "not_applicable")]:
                    copy(f"{target}/{name}", arm / name, units)
                log = arm / f"locust_{arm.name}.log"
                marker = [(i, line) for i, line in enumerate(log.read_text().splitlines(), 1) if "Shape test stopping" in line]
                if len(marker) != 1:
                    raise ValueError(f"SHAPE_COMPLETION_AMBIGUOUS {shape}/{rep.name}/{arm.name}")
                completions.append({"shape": shape, "repetition": int(rep.name[4:]), "arm": arm.name,
                                    "t0": (arm / "t0.txt").read_text().strip(),
                                    "marker": "Shape test stopping", "source_log_sha256": sha(log),
                                    "source_line": marker[0][0],
                                    "transformation": "Only the completion message retained; logger hostname removed"})
        copy(f"runs/{shape}/STATUS", source / "STATUS")
        runs.append({"shape": shape, "completed_repetitions": completed_reps,
                     "matrix_repetitions_defined": manifest["matrix_repetitions_defined"],
                     "matrix_repetitions_requested": manifest["matrix_repetitions_requested"],
                     "duration_seconds": manifest["duration_minutes"] * 60,
                     "shape_mean_users": manifest["shape_mean_users"],
                     "hpa_no_scale_policy": manifest["hpa_no_scale_policy"],
                     "source_manifest_sha256": sha(manifest_path)})
    fixed_manifest = REPO_ROOT / "k8s/deployment-fixed.yaml"
    hpa_manifest = REPO_ROOT / "k8s/hpa.yaml"
    fixed_text, hpa_text = fixed_manifest.read_text(), hpa_manifest.read_text()
    cpu_request = re.search(r'requests:\s+cpu: "(\d+)m"', fixed_text)
    config = {"floor_replicas": int(re.search(r"minReplicas: (\d+)", hpa_text)[1]),
              "fixed_replicas": int(re.search(r"replicas: (\d+)", fixed_text)[1]),
              "max_replicas": int(re.search(r"maxReplicas: (\d+)", hpa_text)[1]),
              "pod_cpu_request_cores": int(cpu_request[1]) / 1000,
              "hpa_cpu_target_fraction": int(re.search(r"averageUtilization: (\d+)", hpa_text)[1]) / 100}
    write_json("metadata/runs.json", {"declared_config": config, "runs": runs,
               "actual_cpu_platforms": "MISSING", "paired_hardware_inventory": "MISSING",
               "warmup_cpu_comparison_source_logs": "MISSING",
               "config_scope": "Declared repository settings; complete per-arm deployed configuration snapshots were not retained",
               "arm_order": ["hpa_tuned", "hpa_stock", "fixed"],
               "arm_order_scope": "Runner order; resumed ramp runs did not preserve chronological repetition order"},
               [fixed_manifest, hpa_manifest], "Whitelist declared configuration and run scope; omit cloud and pod identities")
    write_json("metadata/completion.json", completions, [], "Extract completion markers from private logs; source hashes and line numbers retained")
    probe = REPO_ROOT / "results/capacity_probe"
    copy("capacity/steps.csv", probe / "steps.csv", "users; requests/second; millicores per pod; ready pods")
    for step in sorted(probe.glob("step-*")):
        copy(f"capacity/{step.name}/locust_stats.csv", step / "locust_stats.csv", "milliseconds; requests; requests/second; bytes")
    original = json.loads((probe / "derivation.json").read_text())
    c, r = original["constants"], original["rates"]
    derived = {"SHAPE_MEAN_USERS": original["SHAPE_MEAN_USERS"], "feasible_U": original["scan"]["feasible_U"],
               "pre_saturation_step": original["pre_saturation_step"], "stopping_step": original["stopping_step"],
               "stop_trigger": original["stop_trigger"], "rps_per_user": r["rps_per_user"], "cpu_per_rps": r["cpu_per_rps"],
               "target_cluster_cores": c["hpa_target_cluster_cores"], "constant_min_unit": c["wc98_constant_min_unit"],
               "flash_peak_to_mean": c["wc98_flash_peak_to_mean"],
               "interpretation": "model-derived calibration parameter; not measured maximum capacity",
               "individual_pod_cpu_observations": "MISSING"}
    write_json("capacity/derivation.json", derived, [probe / "derivation.json", probe / "steps.csv"],
               "Retain calibration inputs/result; omit redundant 5000-candidate evaluation table")
    samples = re.findall(r"CAPACITY_PROBE_CPU_SAMPLE step=(\d+) elapsed_in_step_sec=(\d+) median_millicores=(\d+)", (probe / "probe.log").read_text())
    text = "step,elapsed_in_step_sec,median_millicores\n" + "".join(",".join(row) + "\n" for row in samples)
    write("capacity/cpu_samples.csv", text.encode(), [probe / "probe.log"], units="seconds; millicores per pod",
          operation="Extract recorded per-step CPU medians; no individual pod readings retained")
    rr, rr_min, _ = shapes.load_retailrocket_series(shapes.RR_SERIES)
    for spec in shapes.SHAPE_SPECS:
        shape = spec["shape_name"]
        candidate = shapes.CANDIDATES_DIR / spec["candidate_csv"]
        winner = shapes.read_winner(candidate)
        native, _ = shapes.extract_window_counts(spec, winner, rr, rr_min)
        source = (shapes.WC98_SERIES_DIR / f"server_{int(winner.server_or_series_id):03d}_{winner.local_date}.csv"
                  if spec["dataset"] == "worldcup98" else shapes.RR_SERIES)
        text = "second_offset,request_count\n" + "".join(f"{i},{int(v)}\n" for i, v in enumerate(native))
        write(f"workloads/{shape}_1s.csv", text.encode(), [source, candidate], units="seconds from selected window start; requests/events per second",
              operation="Extract selected aggregate count window using recorded offset; dense zeros follow documented extraction semantics")
        provenance_source = shapes.PROVENANCE_DIR / f"{shape}.json"
        provenance = json.loads(provenance_source.read_text())
        provenance["envelope_claim"] = (
            "The selected aggregate window defines a closed-loop user-count envelope. "
            "Individual source arrivals are not replayed. Delivered arrival statistics "
            "are not established by this package; source Hurst is selection context only.")
        write_json(f"workloads/provenance/{shape}.json", provenance, [provenance_source],
                   "Preserve numeric provenance; replace unsupported Poisson-arrival prose with measurement-scope statement")
        copy(f"workloads/candidates/{spec['candidate_csv']}", candidate)
    for source, name in [(REPO_ROOT / "traces/derived/wc98/extraction_summary.json", "wc98"),
                         (REPO_ROOT / "traces/derived/retailrocket/extraction_summary.json", "retailrocket")]:
        copy(f"workloads/extraction/{name}.json", source)
    write_json("metadata/sources.json", origins, [], "Local source hashes and explicit transformations; paths are provenance identifiers, not public dependencies")
    spec = importlib.util.spec_from_file_location("publication", OUTPUT / "verify.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    verifier.verify(write=True)
    print(f"PUBLIC_EVIDENCE_BUILT complete_arms={len(completions)}")


if __name__ == "__main__":
    main()
