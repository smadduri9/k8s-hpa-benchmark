"""Regression and rejection checks for the static portfolio publication path."""

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_portfolio_site as checks
import generate_portfolio_site as site


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.headline = json.loads(site.HEADLINE.read_text())

    def test_current_site(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/check_portfolio_site.py")],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_each_scalar_is_required_and_flows_into_output(self):
        original = site.render(self.headline)
        for group, fields in site.REQUIRED.items():
            for field in fields:
                with self.subTest(group=group, field=field):
                    changed = copy.deepcopy(self.headline)
                    del changed[group][field]
                    with self.assertRaisesRegex(ValueError, "SITE_REQUIRED_FIELD_MISSING"):
                        site.render(changed)
                    changed[group][field] = self.headline[group][field] + 1
                    self.assertNotEqual(site.render(changed), original)

    def test_workload_ranges_and_counts_are_derived(self):
        for name in site.WORKLOADS:
            changed = copy.deepcopy(self.headline)
            changed["replica_scaling"]["workloads"][name]["hpa_tuned"][0] = 99
            values = site.values_from_headline(changed)
            self.assertTrue(values[f"workloads.{name}.peak"].endswith("–99"))
            del changed["replica_scaling"]["workloads"][name]["hpa_stock"]
            with self.assertRaisesRegex(ValueError, "SITE_REQUIRED_FIELD_MISSING"):
                site.render(changed)

    def test_missing_nonfinite_and_malformed_inputs_fail(self):
        for bad in ("MISSING", None, True, float("nan"), float("inf"), -1):
            changed = copy.deepcopy(self.headline)
            changed["flash"]["fixed_p95_median_ms"] = bad
            with self.assertRaisesRegex(ValueError, "SITE_REQUIRED_VALUE_INVALID"):
                site.render(changed)
        changed = copy.deepcopy(self.headline)
        changed["replica_scaling"]["workloads"]["wc98_flash"]["hpa_tuned"] = []
        with self.assertRaisesRegex(ValueError, "SITE_REPLICA_SERIES_INVALID"):
            site.render(changed)

    def test_escape_values_and_reject_unknown_placeholders(self):
        self.assertEqual(site.fill_template('{{ value }}', {"value": '<a title="x">&'}),
                         '&lt;a title=&quot;x&quot;&gt;&amp;')
        with self.assertRaisesRegex(ValueError, "SITE_TEMPLATE_VALUE_MISSING"):
            site.fill_template("{{ missing }}", {})
        with self.assertRaisesRegex(ValueError, "SITE_TEMPLATE_TOKEN_INVALID"):
            site.fill_template("{{ broken token }}", {})

    def test_independent_headline_literals_are_rejected(self):
        template = site.TEMPLATE.read_text()
        checks.check_template(template)
        for literal in ("510", "400", "380", "69", "50.4", "0.03125"):
            with self.subTest(literal=literal), self.assertRaisesRegex(ValueError, "SITE_HARDCODED_NUMBER"):
                checks.check_template(template.replace("</main>", f"<p>{literal}</p></main>"))

    def test_site_rejects_broken_structure_and_claims(self):
        original = site.render()
        mutations = (
            (original.replace('id="results"', 'id="absent"'), "SITE_SECTION_MISSING"),
            (original.replace('href="#results"', 'href="#absent"'), "SITE_ANCHOR_MISSING"),
            (original.replace("assets/css/site.css", "assets/css/absent.css"), "SITE_LINK_NOT_PUBLIC"),
            (original.replace("</main>", "<h1>Another title</h1></main>"), "SITE_H1_COUNT"),
            (original.replace("</main>", "<p>Only flash autoscaled</p></main>"), "SITE_UNSUPPORTED_CLAIM"),
            (original.replace("</main>", '<script src="https://example.com/x.js"></script></main>'), "SITE_RUNTIME_DEPENDENCY"),
            (original.replace('name="description"', 'name="absent"'), "SITE_METADATA_MISSING"),
        )
        for body, error in mutations:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                checks.check_html(body)

    def test_generation_is_deterministic_and_check_rejects_stale_output(self):
        import tempfile
        from unittest.mock import patch

        self.assertEqual(site.render(), site.render())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "index.html"
            with patch.object(site, "OUTPUT", target):
                site.generate()
                site.generate(check=True)
                target.write_text(target.read_text().replace("510", "511", 1))
                with self.assertRaisesRegex(ValueError, "SITE_OUTPUT_STALE"):
                    site.generate(check=True)


if __name__ == "__main__":
    unittest.main()
