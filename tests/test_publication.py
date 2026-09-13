"""Offline publication regression and rejection tests; no cluster required."""

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "artifacts/v1.1"
spec = importlib.util.spec_from_file_location("publication", BUNDLE / "verify.py")
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


class PublicationTests(unittest.TestCase):
    def test_current_measurements(self):
        h = publication.verify()
        f, t = h["flash"], h["ready_pod_time"]
        self.assertEqual([f[f"{a}_p95_median_ms"] for a in ("fixed", "tuned", "stock")], [510, 400, 380])
        self.assertEqual([f["repetitions"], f["tuned_better_than_fixed_reps"], f["stock_better_than_fixed_reps"]], [6, 6, 6])
        self.assertEqual([f["tuned_vs_fixed_wilcoxon_p"], f["stock_vs_fixed_wilcoxon_p"], f["tuned_vs_stock_wilcoxon_p"]], [.03125, .03125, .09375])
        for arm, expected, n in (("fixed", 1.1855555555555555, 6), ("tuned", 1.783611111111111, 5), ("stock", 2.235138888888889, 6)):
            self.assertAlmostEqual(t[f"{arm}_median_pod_hours"], expected)
            self.assertEqual(t[f"{arm}_n"], n)
        self.assertAlmostEqual(t["tuned_vs_fixed_ratio_of_medians_pct"], 50.44517338331771)
        self.assertAlmostEqual(t["stock_vs_fixed_ratio_of_medians_pct"], 88.53092783505154)
        expected = {"wc98_flash": ([12, 11, 11, 11, 10, 11], [11, 12, 12, 11, 11, 11]),
                    "wc98_ramp": ([8, 4], [8, 5]), "wc98_constant": ([4], [4]),
                    "wc98_periodic": ([5], [5]), "rr_periodic": ([5], [5])}
        for shape, (tuned, stock) in expected.items():
            data = h["replica_scaling"]["workloads"][shape]
            self.assertEqual(data["hpa_tuned"], tuned)
            self.assertEqual(data["hpa_stock"], stock)
            self.assertEqual(data["fixed"], [4]*len(tuned))
        self.assertEqual(h["capacity"]["shape_mean_users"], 69)
        reps = publication.rows(BUNDLE / "summary/flash_repetitions.csv")
        for arm, expected in (("fixed", [480, 550, 510, 470, 510, 530]),
                              ("hpa_tuned", [440, 400, 430, 380, 370, 400]),
                              ("hpa_stock", [410, 380, 400, 370, 380, 370])):
            self.assertEqual([float(r["client_p95_ms"]) for r in reps if r["arm"] == arm], expected)

    def test_signed_ranks_ties_and_zeros(self):
        self.assertEqual(publication.wilcoxon([1, 2, 3], [1, 2, 3])["p_two_sided"], 1)
        result = publication.wilcoxon([0, 0, 0, 0], [1, -1, 2, 0])
        self.assertEqual(result["statistic"], 1.5)
        self.assertEqual(result["p_two_sided"], .75)
        self.assertEqual(result["zero_differences"], 1)

    def test_contamination_is_retained_but_not_integrated(self):
        directory = BUNDLE / "runs/wc98_flash/rep-1/hpa_tuned"
        path = directory / "replica_series_hpa.csv"
        start = publication.epoch((directory / "t0.txt").read_text().strip())
        result = publication.replica_measurements(path, start, 1080, True)
        self.assertEqual(result["ready_pod_hours"], "MISSING")
        self.assertEqual(result["samples_total"], 4991)
        self.assertEqual(result["samples_in_window"], 140)
        self.assertEqual(result["series_span_seconds"], 76287)
        with self.assertRaisesRegex(ValueError, "UNEXCLUDED_SERIES_OUTSIDE_WINDOW"):
            publication.replica_measurements(path, start, 1080, False)

    def test_left_sample_integral_and_window_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "series.csv"
            path.write_text("timestamp,spec_replicas,status_replicas,ready_replicas\n"
                            "2026-01-01T00:00:00Z,4,4,4\n"
                            "2026-01-01T00:00:15Z,8,8,8\n"
                            "2026-01-01T00:00:30Z,4,4,4\n")
            start = publication.epoch("2026-01-01T00:00:00Z")
            self.assertEqual(publication.replica_measurements(path, start, 45, False)["ready_pod_hours"], .05)
            with self.assertRaisesRegex(ValueError, "INCOMPLETE_REPLICA_WINDOW"):
                publication.replica_measurements(path, start, 120, False)

    def test_standalone_bundle_and_tamper_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "evidence"
            shutil.copytree(BUNDLE, target)
            command = [sys.executable, "-I", "-B", str(target / "verify.py")]
            result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            path = target / "summary/headline.json"
            value = json.loads(path.read_text())
            value["flash"]["fixed_p95_median_ms"] += 1
            path.write_text(json.dumps(value))
            result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SUMMARY_MISMATCH", result.stderr)

    def test_policy_and_raw_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "evidence"
            shutil.copytree(BUNDLE, target)
            policy = target / "exclusions.csv"
            original = policy.read_text()
            policy.write_text(original.replace("ready_pod_hours,EXCLUDE", "ready_pod_hours,KEEP"))
            with self.assertRaisesRegex(ValueError, "UNEXCLUDED_SERIES_OUTSIDE_WINDOW"):
                publication.compute(target)
            policy.write_text(original)
            path = target / "runs/wc98_ramp/rep-1/fixed/STATUS"
            path.write_text("PASS\nunreviewed addition\n")
            with self.assertRaisesRegex(ValueError, "MANIFEST_MISMATCH"):
                publication.verify(target)

    def test_incomplete_rep_and_calibration_disagreement_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "evidence"
            shutil.copytree(BUNDLE, target)
            extra = target / "runs/wc98_ramp/rep-3/hpa_tuned"
            extra.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "ARM_INVENTORY_MISMATCH"):
                publication.compute(target)
            extra.rmdir()
            extra.parent.rmdir()
            path = target / "capacity/cpu_samples.csv"
            path.write_text(path.read_text().replace(",680\n", ",681\n"))
            with self.assertRaisesRegex(ValueError, "PROBE_CPU_SAMPLE_MISMATCH"):
                publication.compute(target)

    def test_links_and_code_secrets_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "evidence"
            shutil.copytree(BUNDLE, target)
            (target / "external").symlink_to(Path(directory))
            with self.assertRaisesRegex(ValueError, "SYMLINK_NOT_ALLOWED"):
                publication.verify(target)
            (target / "external").unlink()
            path = target / "example.py"
            path.write_text("token = '" + "gh" + "p_" + "x"*30 + "'\n")
            with self.assertRaisesRegex(ValueError, "SECURITY_SCAN_FAILED example.py:token"):
                publication.security_scan(target)

    def test_generated_documentation_matches(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/generate_publication_text.py"), "--check"],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
