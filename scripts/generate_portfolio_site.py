#!/usr/bin/env python3
"""Render the static Pages home from canonical metrics; --check rejects drift.

OS/arch assumptions: Python 3.14, macOS or Linux; standard library only.
"""

import argparse
import html
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADLINE = ROOT / "artifacts/v1.1/summary/headline.json"
TEMPLATE = ROOT / "docs/site/index.template.html"
OUTPUT = ROOT / "docs/index.html"
TOKEN = re.compile(r"{{\s*([a-z0-9_.]+)(?:\|([+.0-9fg]+))?\s*}}")
WORKLOADS = ("wc98_constant", "wc98_periodic", "rr_periodic", "wc98_ramp", "wc98_flash")
REQUIRED = {
    "capacity": ("shape_mean_users",),
    "flash": ("fixed_p95_median_ms", "tuned_p95_median_ms", "stock_p95_median_ms",
              "repetitions", "tuned_better_than_fixed_reps", "stock_better_than_fixed_reps",
              "tuned_vs_fixed_wilcoxon_p", "stock_vs_fixed_wilcoxon_p", "tuned_vs_stock_wilcoxon_p"),
    "ready_pod_time": ("fixed_median_pod_hours", "tuned_median_pod_hours", "stock_median_pod_hours",
                       "fixed_n", "tuned_n", "stock_n", "tuned_vs_fixed_ratio_of_medians_pct",
                       "stock_vs_fixed_ratio_of_medians_pct"),
    "replica_scaling": ("floor_replicas",),
}


def number(value, path):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"SITE_REQUIRED_VALUE_INVALID {path}")
    return value


def values_from_headline(headline):
    values = {}
    try:
        for group, fields in REQUIRED.items():
            for field in fields:
                path = f"{group}.{field}"
                values[path] = number(headline[group][field], path)
        basis = headline["capacity"]["basis"]
        if basis != "model-derived calibration parameter":
            raise ValueError("SITE_CALIBRATION_BASIS_INVALID")
        values["capacity.basis"] = basis
        for name in WORKLOADS:
            data = headline["replica_scaling"]["workloads"][name]
            repetitions = data["repetitions"]
            if not isinstance(repetitions, list) or not repetitions:
                raise ValueError(f"SITE_REPETITIONS_INVALID {name}")
            for rep in repetitions:
                number(rep, name)
            for arm in ("fixed", "hpa_tuned", "hpa_stock"):
                series = data[arm]
                if not isinstance(series, list) or len(series) != len(repetitions):
                    raise ValueError(f"SITE_REPLICA_SERIES_INVALID {name}/{arm}")
                for peak in series:
                    number(peak, name)
            peaks = data["hpa_tuned"] + data["hpa_stock"]
            low, high = min(peaks), max(peaks)
            values[f"workloads.{name}.peak"] = f"{low:g}" if low == high else f"{low:g}–{high:g}"
            values[f"workloads.{name}.n"] = len(repetitions)
    except (KeyError, TypeError) as error:
        raise ValueError(f"SITE_REQUIRED_FIELD_MISSING {error}") from error
    return values


def fill_template(template, values):
    def replace(match):
        path, style = match.groups()
        if path not in values:
            raise ValueError(f"SITE_TEMPLATE_VALUE_MISSING {path}")
        value = values[path]
        rendered = format(value, style) if style else str(value)
        return html.escape(rendered, quote=True)

    rendered = TOKEN.sub(replace, template)
    if "{{" in rendered or "}}" in rendered:
        raise ValueError("SITE_TEMPLATE_TOKEN_INVALID")
    return rendered


def render(headline=None, template=None):
    if headline is None:
        headline = json.loads(HEADLINE.read_text(encoding="utf-8"))
    if template is None:
        template = TEMPLATE.read_text(encoding="utf-8")
    values = values_from_headline(headline)
    bundle = HEADLINE.parents[1]
    manifest = json.loads((bundle / "MANIFEST.json").read_text(encoding="utf-8"))
    # Package size is file metadata, separate from experimental measurements.
    total_bytes = sum(entry["bytes"] for entry in manifest["artifacts"])
    total_bytes += sum((bundle / name).stat().st_size for name in ("MANIFEST.json", "SHA256SUMS"))
    values["evidence.size_mib"] = total_bytes / (1024 * 1024)
    return fill_template(template, values)


def generate(check=False):
    expected = render()
    if check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise ValueError("SITE_OUTPUT_STALE: run scripts/generate_portfolio_site.py")
    else:
        OUTPUT.write_text(expected, encoding="utf-8", newline="\n")
    print("PORTFOLIO_SITE_PASS canonical=headline.json output=docs/index.html")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generate(args.check)


if __name__ == "__main__":
    main()
