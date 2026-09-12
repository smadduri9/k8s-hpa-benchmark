#!/usr/bin/env python3
"""Check active documentation links, engineering SVGs and public-file privacy.

OS/arch assumptions: Python 3.14, macOS or Linux; Git and standard library only.
External URLs require a separate network check; local checks also work offline.
"""

import html
import ipaddress
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = (
    "README.md", "RESULTS.md", "POSTMORTEM.md", "DATA_PROVENANCE.md",
    "REPRODUCE.md", "CONTRIBUTING.md", "docs/README.md",
    "docs/MEASUREMENT_GUARDS.md", "docs/COLD_START.md", "docs/SHAPE_SELECTION.md",
    "docs/error-budget-policy.md", "docs/phase5-bucket-schema.md", "docs/site/README.md",
)
REPOSITORY = "https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/"
SVG_NS = "{http://www.w3.org/2000/svg}"


def public_files():
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, check=True, capture_output=True, timeout=30,
    )
    return {name for name in result.stdout.decode().split("\0")
            if name and (ROOT / name).is_file()}


def headings(path):
    slugs = set()
    counts = {}
    for heading in re.findall(r"^#{1,6}\s+(.+)$", path.read_text(), re.MULTILINE):
        heading = html.unescape(heading).lower().replace("`", "")
        slug = re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")
        duplicate = counts.get(slug, 0)
        counts[slug] = duplicate + 1
        slugs.add(f"{slug}-{duplicate}" if duplicate else slug)
    slugs.update(re.findall(r'(?:id|name)="([^"]+)"', path.read_text()))
    return slugs


def check_links(files):
    checked = 0
    external = set()
    for name in ACTIVE:
        source = ROOT / name
        body = re.sub(r"```.*?```", "", source.read_text(), flags=re.DOTALL)
        for raw in re.findall(r"\]\(([^)]+)\)", body):
            link = raw.split(' "', 1)[0].strip("<>")
            if link.startswith(REPOSITORY):
                link = link.removeprefix(REPOSITORY)
                base = ROOT
            else:
                base = source.parent
            parsed = urlsplit(link)
            if parsed.scheme in {"https", "http", "mailto"}:
                external.add(link)
                continue
            if parsed.scheme or parsed.netloc:
                raise ValueError(f"UNSUPPORTED_LINK {name}: {link}")
            target = (base / unquote(parsed.path)).resolve() if parsed.path else source
            relative = target.relative_to(ROOT).as_posix()
            public = relative in files or (target.is_dir() and any(
                f.startswith(relative + "/") for f in files))
            if not public:
                raise ValueError(f"NONPUBLIC_LINK {name}: {link}")
            if parsed.fragment and target.suffix == ".md":
                if unquote(parsed.fragment) not in headings(target):
                    raise ValueError(f"BROKEN_ANCHOR {name}: {link}")
            checked += 1
    print(f"PUBLIC_LINKS_PASS documents={len(ACTIVE)} local_targets={checked} external_urls={len(external)}")


def check_diagrams():
    for name in ("architecture.svg", "measurement_guards.svg"):
        path = ROOT / "docs/assets/figures" / name
        svg = ET.parse(path).getroot()
        if svg.tag != SVG_NS + "svg" or not svg.get("viewBox") or svg.get("width") != "100%":
            raise ValueError(f"SVG_NOT_RESPONSIVE {name}")
        for tag in ("title", "desc"):
            element = svg.find(SVG_NS + tag)
            if element is None or not element.text or element.get("id") != tag:
                raise ValueError(f"SVG_ACCESSIBILITY_MISSING {name}: {tag}")
        if svg.get("aria-labelledby") != "title desc":
            raise ValueError(f"SVG_ACCESSIBILITY_MISSING {name}: aria-labelledby")
        for element in svg.iter():
            tag = element.tag.removeprefix(SVG_NS)
            if tag in {"script", "foreignObject", "image", "linearGradient", "radialGradient"}:
                raise ValueError(f"SVG_UNEXPECTED_ELEMENT {name}: {tag}")
            if any(key.endswith("href") and not value.startswith("#")
                   for key, value in element.attrib.items()):
                raise ValueError(f"SVG_EXTERNAL_REFERENCE {name}")
    print("ENGINEERING_SVG_PASS diagrams=2")


def check_privacy(files):
    patterns = {
        "local_identity_path": r"/(?:Users|home|private/var|var/folders)/[^\s]+",
        "machine_account": r"[\w.-]+@[\w.-]+(?:\.local|\.internal)\b|\bMac\.local\b",
        "project_identity": r"\bprojects/[a-z][a-z0-9-]{5,}|\bPROJECT_ID=[a-z][a-z0-9-]{5,}",
        "private_registry": r"(?:[\w-]+-docker\.pkg\.dev|gcr\.io)/[a-z][a-z0-9-]+/",
        "token": r"AIza[\w-]{30,}|gh[pousr]_[\w]{20,}|ya29\.[\w-]{15,}|AKIA[A-Z0-9]{16}",
        "private_key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    }
    text_files = 0
    for name in sorted(files):
        data = (ROOT / name).read_bytes()
        searchable = data.decode("utf-8", errors="ignore")
        for category, pattern in patterns.items():
            if re.search(pattern, searchable):
                raise ValueError(f"PUBLIC_PRIVACY_FAILED {name}: {category}")
        if b"\0" in data:
            continue
        try:
            body = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        text_files += 1
        for match in re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", body):
            try:
                address = ipaddress.ip_address(match)
            except ValueError:
                continue
            if not address.is_loopback and not address.is_unspecified:
                raise ValueError(f"PUBLIC_PRIVACY_FAILED {name}: service_ip")
    print(f"PUBLIC_PRIVACY_PASS files={len(files)} text_files={text_files}")


def main():
    files = public_files()
    check_links(files)
    check_diagrams()
    check_privacy(files)
    print("PUBLIC_DOCS_PASS")


if __name__ == "__main__":
    main()
