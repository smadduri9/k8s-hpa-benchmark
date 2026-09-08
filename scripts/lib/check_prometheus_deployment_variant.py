#!/usr/bin/env python3
"""Fail if prometheus deployment.yaml and deployment-gke.yaml drift beyond volumes."""

from __future__ import annotations

import pathlib
import sys

CHECK_NAME = "PROMETHEUS_DEPLOYMENT_VARIANT_DRIFT_CHECK"
STRIP_KEYS = ("volumeMounts", "volumes")


def load_deployment_document(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.split("---", 1)[0].strip() + "\n"


def strip_yaml_sections(doc: str, keys: tuple[str, ...]) -> str:
    lines = doc.splitlines()
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        matched = False
        for key in keys:
            if stripped.startswith(f"{key}:"):
                matched = True
                index += 1
                while index < len(lines):
                    next_line = lines[index]
                    if not next_line.strip():
                        index += 1
                        continue
                    next_indent = len(next_line) - len(next_line.lstrip())
                    if next_indent <= indent:
                        break
                    index += 1
                break
        if not matched:
            out.append(line)
            index += 1
    return "\n".join(out) + "\n"


def main() -> int:
    if len(sys.argv) != 3:
        print(
            f"usage: {pathlib.Path(sys.argv[0]).name} "
            "<deployment.yaml> <deployment-gke.yaml>",
            file=sys.stderr,
        )
        return 2

    local_path = pathlib.Path(sys.argv[1])
    gke_path = pathlib.Path(sys.argv[2])
    local_norm = strip_yaml_sections(load_deployment_document(local_path), STRIP_KEYS)
    gke_norm = strip_yaml_sections(load_deployment_document(gke_path), STRIP_KEYS)

    if local_norm != gke_norm:
        print(f"{CHECK_NAME}=FAIL", file=sys.stderr)
        print(
            f"ERROR: {CHECK_NAME} files differ outside volumeMounts/volumes",
            file=sys.stderr,
        )
        for line_num, (left, right) in enumerate(
            zip(local_norm.splitlines(), gke_norm.splitlines()), start=1
        ):
            if left != right:
                print(f"  line {line_num} local: {left}", file=sys.stderr)
                print(f"  line {line_num} gke:   {right}", file=sys.stderr)
        local_lines = local_norm.splitlines()
        gke_lines = gke_norm.splitlines()
        if len(local_lines) != len(gke_lines):
            print(
                f"  line counts differ: local={len(local_lines)} gke={len(gke_lines)}",
                file=sys.stderr,
            )
        return 1

    print(f"{CHECK_NAME}=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
