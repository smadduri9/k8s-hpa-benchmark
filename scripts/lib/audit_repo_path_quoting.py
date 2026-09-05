#!/usr/bin/env python3
"""Detect unquoted $REPO_ROOT / ${REPO_ROOT} expansions in shell scripts."""

from __future__ import annotations

import sys
from pathlib import Path


def join_continued_lines(lines: list[str]) -> list[str]:
    joined: list[str] = []
    buffer = ""
    for line in lines:
        if buffer:
            buffer += line.lstrip()
        else:
            buffer = line
        if buffer.rstrip().endswith("\\"):
            buffer = buffer.rstrip()[:-1] + " "
            continue
        joined.append(buffer)
        buffer = ""
    if buffer:
        joined.append(buffer)
    return joined


def is_position_quoted(line: str, pos: int) -> bool:
    in_single = False
    in_double = False
    paren_depth = 0
    i = 0
    while i < pos:
        ch = line[i]
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if paren_depth > 0:
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth -= 1
            i += 1
            continue
        if in_double:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_double = False
                i += 1
                continue
            if ch == "$" and i + 1 < len(line) and line[i + 1] == "(":
                paren_depth = 1
                i += 2
                continue
            i += 1
            continue
        if ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch == "$" and i + 1 < len(line) and line[i + 1] == "(":
            paren_depth = 1
            i += 2
            continue
        i += 1
    return in_single or in_double or paren_depth > 0


def find_unquoted_expansions(line: str) -> list[int]:
    if not line.strip() or line.lstrip().startswith("#"):
        return []

    positions: list[int] = []
    idx = 0
    while idx < len(line):
        brace = line.find("${REPO_ROOT}", idx)
        plain = line.find("$REPO_ROOT", idx)
        candidates = [p for p in (brace, plain) if p != -1]
        if not candidates:
            break
        pos = min(candidates)
        if not is_position_quoted(line, pos):
            positions.append(pos)
        idx = pos + 1
    return positions


def find_function_line_range(lines: list[str], func_name: str) -> tuple[int, int] | None:
    start = None
    for idx, line in enumerate(lines):
        if line.startswith(f"{func_name}()") and "{" in line:
            start = idx
            break
    if start is None:
        return None

    depth = 0
    for idx in range(start, len(lines)):
        depth += lines[idx].count("{") - lines[idx].count("}")
        if idx > start and depth <= 0:
            return (start + 1, idx + 1)
    return None


def audit_scripts(scripts_dir: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(scripts_dir.rglob("*.sh")):
        raw_lines = path.read_text(encoding="utf-8").splitlines()
        lines = join_continued_lines(raw_lines)
        skip_range = None
        if path.name == "preflight.sh":
            skip_range = find_function_line_range(raw_lines, "run_repo_path_whitespace_audit")

        for lineno, line in enumerate(lines, start=1):
            if skip_range and skip_range[0] <= lineno <= skip_range[1]:
                continue
            if find_unquoted_expansions(line):
                violations.append(f"file={path} line={lineno}: {line}")
    return violations


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: audit_repo_path_quoting.py <repo_root>", file=sys.stderr)
        return 2

    repo_root = Path(sys.argv[1]).resolve()
    repo_text = str(repo_root)
    if " " not in repo_text and "\t" not in repo_text:
        print("repo_path_whitespace_audit=SKIPPED")
        return 0

    print(f'repo_path_whitespace_audit=ACTIVE path="{repo_text}"')
    violations = audit_scripts(repo_root / "scripts")
    for item in violations:
        print(f"UNQUOTED_REPO_ROOT {item}", file=sys.stderr)
    if violations:
        return 1
    print("repo_path_whitespace_audit=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
