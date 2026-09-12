#!/usr/bin/env python3
"""Check Pages structure, canonical generation, assets, links and claim boundaries.

OS/arch assumptions: Python 3.14, macOS or Linux; standard library and Git only.
Network checks are intentionally separate so CI works offline.
"""

import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from check_public_docs import ROOT, headings, public_files
from generate_portfolio_site import OUTPUT, TEMPLATE, TOKEN, generate

REPO = "https://github.com/smadduri9/k8s-hpa-benchmark"
CANONICAL = "https://smadduri9.github.io/k8s-hpa-benchmark/"
SECTIONS = {"main", "top", "results", "hpa", "workloads", "resources", "postmortem",
            "guards", "architecture", "provenance", "evidence", "technical", "evolution"}
FIGURES = {"latency_p95_medians.svg", "latency_p95_by_rep.svg", "replica_scaling.svg",
           "ready_pod_hours.svg", "architecture.svg", "measurement_guards.svg"}
BANNED = ("only flash autoscaled", "stock is definitively better", "statistically equivalent",
          "ready-pod time is compute cost", "69 is measured maximum capacity",
          "proves universal hpa behavior", "peak-to-mean ratio determines scaling")
# Authored numbers are limited to navigation, configuration, and sourced context.
# New experimental measurements must enter through a canonical template token.
STATIC_NUMBERS = {
    *[f"{i:02}" for i in range(1, 10)],
    "8", "98", "1998",  # Project and dataset names.
    "12", "60", "95",  # Declared HPA settings, coverage gate, and p95 definition.
    "51.7", "0.97",  # Explicitly withdrawn historical claim.
    "1.35", "4.0",  # Attributed dataset size and third-party license version.
    "1", "5",  # Excluded rep-1 and historical Phase 5 identifier.
    "1.1", "1.0.0", "10.5281", "22697397",  # Evidence version and existing DOI.
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.links = []
        self.assets = []
        self.images = []
        self.headings = []
        self.meta = {}
        self.canonical = []
        self.tags = []
        self.text = []
        self.language = None

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        self.tags.append(tag)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "html":
            self.language = attrs.get("lang")
        if re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        if tag == "a":
            require(bool(attrs.get("href")), "SITE_LINK_EMPTY")
            self.links.append(attrs["href"])
        if tag == "meta":
            self.meta[attrs.get("name", attrs.get("property"))] = attrs.get("content")
        if tag == "link":
            if attrs.get("rel") == "canonical":
                self.canonical.append(attrs.get("href"))
            elif attrs.get("href"):
                self.assets.append(attrs["href"])
        if "src" in attrs:
            self.assets.append(attrs["src"])
        if tag == "img":
            self.images.append(attrs)
        require(not any(key.startswith("on") for key in attrs), "SITE_INLINE_HANDLER")
        require("style" not in attrs, "SITE_INLINE_STYLE")

    def handle_data(self, data):
        self.text.append(data)


def check_html(body, files=None):
    page = Page()
    page.feed(body)
    require(page.language == "en", "SITE_LANGUAGE_MISSING")
    require(page.headings.count(1) == 1, "SITE_H1_COUNT")
    require(all(b <= a + 1 for a, b in zip(page.headings, page.headings[1:])), "SITE_HEADING_SKIP")
    require(len(page.ids) == len(set(page.ids)), "SITE_DUPLICATE_ID")
    require(SECTIONS <= set(page.ids), "SITE_SECTION_MISSING")
    require({"header", "nav", "main", "footer", "title"} <= set(page.tags), "SITE_LANDMARK_MISSING")
    require(not {"script", "iframe", "object", "embed", "base"} & set(page.tags), "SITE_RUNTIME_DEPENDENCY")
    require(page.canonical == [CANONICAL], "SITE_CANONICAL_INVALID")
    for name in ("description", "viewport", "og:title", "og:description", "og:url", "og:type",
                 "twitter:card", "twitter:title", "twitter:description", "theme-color"):
        require(bool(page.meta.get(name)), f"SITE_METADATA_MISSING {name}")
    require(page.meta["og:url"] == CANONICAL, "SITE_OG_URL_INVALID")
    text = " ".join(" ".join(page.text).lower().split())
    for claim in BANNED:
        require(claim not in text, f"SITE_UNSUPPORTED_CLAIM {claim}")
    require(not re.search(r"/(?:Users|home|tmp|private/var|var/folders)/|file://", body), "SITE_LOCAL_PATH")
    for image in page.images:
        require(bool(image.get("alt", "").strip()), "SITE_IMAGE_ALT_MISSING")
        require(image.get("width") and image.get("height"), "SITE_IMAGE_DIMENSIONS_MISSING")
    require({urlsplit(i["src"]).path.rsplit("/", 1)[-1] for i in page.images} == FIGURES,
            "SITE_FIGURE_MISSING")
    files = public_files() if files is None else files
    for asset in page.assets:
        parsed = urlsplit(asset)
        require(not parsed.scheme and not parsed.netloc and not asset.startswith("/"),
                f"SITE_EXTERNAL_ASSET {asset}")
    checked = 0
    external = set()
    for link in page.links + page.assets:
        parsed = urlsplit(link)
        target = None
        for kind in ("blob", "tree"):
            prefix = f"{REPO}/{kind}/main/"
            if link.startswith(prefix):
                target = ROOT / unquote(urlsplit(link.removeprefix(prefix)).path)
        if parsed.scheme or parsed.netloc:
            require(parsed.scheme == "https", f"SITE_UNSUPPORTED_LINK {link}")
            external.add(link)
            if target is None:
                continue
        else:
            require(not parsed.path.startswith("/"), f"SITE_ROOT_RELATIVE_LINK {link}")
            target = OUTPUT.parent / unquote(parsed.path) if parsed.path else OUTPUT
        relative = target.resolve().relative_to(ROOT).as_posix()
        require(relative in files or (target.is_dir() and any(f.startswith(relative + "/") for f in files)),
                f"SITE_LINK_NOT_PUBLIC {link}")
        if parsed.fragment:
            ids = set(page.ids) if target == OUTPUT else headings(target)
            require(unquote(parsed.fragment) in ids, f"SITE_ANCHOR_MISSING {link}")
        checked += 1
    return page, checked, external


def check_styles_and_figures(page):
    css = (ROOT / "docs/assets/css/site.css").read_text(encoding="utf-8")
    require(not re.search(r"@import|url\s*\(", css, re.I), "SITE_CSS_EXTERNAL_RESOURCE")
    for feature in (":focus-visible", "prefers-color-scheme: dark", "prefers-reduced-motion"):
        require(feature in css, f"SITE_ACCESSIBILITY_STYLE_MISSING {feature}")
    ns = "{http://www.w3.org/2000/svg}"
    for image in page.images:
        svg = ET.parse(OUTPUT.parent / image["src"]).getroot()
        require(svg.get("viewBox") and svg.find(ns + "title") is not None
                and svg.find(ns + "desc") is not None, f"SITE_SVG_ACCESSIBILITY {image['src']}")
        for element in svg.iter():
            require(element.tag not in {ns + "script", ns + "foreignObject"}, "SITE_SVG_SCRIPT")
            require(not any(k.endswith("href") and not v.startswith("#") for k, v in element.attrib.items()),
                    "SITE_SVG_EXTERNAL_RESOURCE")


def check_template(template):
    page = Page()
    page.feed(TOKEN.sub("CANONICAL_VALUE", template))
    text = " ".join(page.text + [image.get("alt", "") for image in page.images])
    authored = set(re.findall(r"\d+(?:\.\d+)*", text))
    require(authored <= STATIC_NUMBERS, f"SITE_HARDCODED_NUMBER {sorted(authored - STATIC_NUMBERS)}")


def main():
    check_template(TEMPLATE.read_text(encoding="utf-8"))
    generate(check=True)
    page, checked, external = check_html(OUTPUT.read_text(encoding="utf-8"))
    check_styles_and_figures(page)
    print(f"PORTFOLIO_STRUCTURE_PASS sections={len(SECTIONS)} figures={len(page.images)} h1=1")
    print(f"PORTFOLIO_LINKS_PASS local_and_repository_targets={checked} external_urls={len(external)}")
    print("PORTFOLIO_ACCESSIBILITY_PASS semantic_structure alt_text focus_styles reduced_motion")
    print("PORTFOLIO_RUNTIME_PASS scripts=0 remote_assets=0")


if __name__ == "__main__":
    main()
