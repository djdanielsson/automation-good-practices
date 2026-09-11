#!/usr/bin/env python3
"""Validate AsciiDoc example blocks follow repository conventions."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

SOURCE_BLOCK_RE = re.compile(
    r"^\[(?P<attrs>source[^\]]*)\]\s*$",
    re.IGNORECASE,
)
INCLUDE_RE = re.compile(r"^include::([^[\]]+)(?:\[[^\]]*\])?\s*$")
TITLE_RE = re.compile(r"^\.(?P<title>.+?)\s*$")

DO_TITLE_RE = re.compile(
    r"^\.(?:Do this(?:\b|:)|.*\bgood\b.*)",
    re.IGNORECASE,
)
DONT_TITLE_RE = re.compile(
    r"^\.(?:Don't do this|Don’t do this|instead of this|Bad:|Bad\b)",
    re.IGNORECASE,
)
BAD_PATH_RE = re.compile(
    r"(?:^|[/_])(?:bad|_bad|dont_use_groups)(?:[._/]|$)",
    re.IGNORECASE,
)


class ExampleKind(str, Enum):
    DO = "do"
    DONT = "dont"


@dataclass(frozen=True)
class SourceBlock:
    source: Path
    line: int
    attrs: str
    title: str
    title_line: int
    content_lines: tuple[str, ...]


def parse_source_attrs(attrs: str) -> tuple[str | None, bool]:
    parts = [part.strip() for part in attrs.split(",")]
    language = None
    lint_skip = False
    for part in parts:
        lowered = part.lower()
        if lowered == "source":
            continue
        if lowered == "lint=skip":
            lint_skip = True
            continue
        language = part.strip()
    return language, lint_skip


def classify_title(title: str) -> ExampleKind | None:
    if not title:
        return None
    if DONT_TITLE_RE.match(title):
        return ExampleKind.DONT
    if DO_TITLE_RE.match(title):
        return ExampleKind.DO
    if BAD_PATH_RE.search(title):
        return ExampleKind.DONT
    if re.search(r"\bgood\b", title, re.IGNORECASE):
        return ExampleKind.DO
    if re.search(r"\bbad\b", title, re.IGNORECASE):
        return ExampleKind.DONT
    return None


def find_title(lines: list[str], block_start: int) -> tuple[str, int]:
    for index in range(block_start - 2, max(block_start - 10, -1), -1):
        match = TITLE_RE.match(lines[index].strip())
        if match:
            return f".{match.group('title')}", index + 1
    return "", 0


def extract_source_blocks(adoc_path: Path) -> list[SourceBlock]:
    lines = adoc_path.read_text(encoding="utf-8").splitlines()
    blocks: list[SourceBlock] = []
    index = 0

    while index < len(lines):
        match = SOURCE_BLOCK_RE.match(lines[index].strip())
        if not match:
            index += 1
            continue

        attrs = match.group("attrs")
        language, _lint_skip = parse_source_attrs(attrs)
        if language not in {"yaml", "yml"}:
            index += 1
            continue

        block_start = index + 1
        index += 1
        if index >= len(lines) or lines[index].strip() != "----":
            continue

        index += 1
        content_lines: list[str] = []
        while index < len(lines) and lines[index].strip() != "----":
            content_lines.append(lines[index])
            index += 1

        if index >= len(lines):
            break

        title, title_line = find_title(lines, block_start)
        blocks.append(
            SourceBlock(
                source=adoc_path.resolve(),
                line=block_start,
                attrs=attrs,
                title=title,
                title_line=title_line,
                content_lines=tuple(content_lines),
            )
        )
        index += 1

    return blocks


def is_inline_content(content_lines: tuple[str, ...]) -> bool:
    if not content_lines:
        return True
    non_empty = [line for line in content_lines if line.strip()]
    if not non_empty:
        return True
    if len(non_empty) == 1:
        return INCLUDE_RE.match(non_empty[0].strip()) is None
    return True


def validate_block(block: SourceBlock) -> list[str]:
    errors: list[str] = []
    _language, lint_skip = parse_source_attrs(block.attrs)
    rel_source = block.source

    kind = classify_title(block.title)
    if kind is None:
        errors.append(
            f"{rel_source}:{block.line}: YAML example block missing a "
            "'.Do this:' or '.Don't do this:' title "
            f"(nearest title: {block.title!r} at line {block.title_line or 'unknown'})"
        )

    if lint_skip:
        return errors

    if is_inline_content(block.content_lines):
        preview = block.content_lines[0] if block.content_lines else "(empty)"
        errors.append(
            f"{rel_source}:{block.line}: inline YAML is not allowed; use "
            f"include::path/to/example.yml[] (found: {preview!r}). "
            "Use [source,yaml,lint=skip] only for non-Ansible YAML that "
            "should stay inline."
        )
        return errors

    include_match = INCLUDE_RE.match(block.content_lines[0].strip())
    if include_match is None:
        errors.append(
            f"{rel_source}:{block.line}: YAML block must contain a single "
            "include:: directive"
        )
        return errors

    include_target = include_match.group(1)
    include_path = (block.source.parent / include_target).resolve()
    if not include_path.is_file():
        errors.append(
            f"{rel_source}:{block.line}: include target not found: {include_target}"
        )

    if kind is ExampleKind.DONT and not BAD_PATH_RE.search(include_target):
        errors.append(
            f"{rel_source}:{block.line}: '.Don't do this:' example should live "
            f"under a bad/ path (include::{include_target}[])"
        )

    return errors


def find_adoc_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix == ".adoc":
            files.append(path.resolve())
            continue
        if path.is_dir():
            files.extend(sorted(path.rglob("*.adoc")))
    return sorted({path for path in files})


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate AsciiDoc YAML example conventions.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path(".")],
        help="AsciiDoc files or directories to scan (default: repository root)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    scan_paths = [repo_root / path if not path.is_absolute() else path for path in args.paths]
    errors: list[str] = []

    for adoc_path in find_adoc_files(scan_paths):
        if adoc_path.name.startswith("_"):
            continue
        for block in extract_source_blocks(adoc_path):
            errors.extend(validate_block(block))

    if errors:
        print("AsciiDoc example check failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("AsciiDoc example check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
