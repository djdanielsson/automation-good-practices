#!/usr/bin/env python3
"""Extract YAML blocks from AsciiDoc sources for linting."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

INCLUDE_RE = re.compile(r"^include::([^[\]]+)\[\]\s*$")
BAD_MARKER_RE = re.compile(r"(?:^|[/_])(?:bad|_bad)(?:[._/]|$)", re.IGNORECASE)
POSITIVE_TITLE_RE = re.compile(
    r"^\.(?:(?:Do this)|.*\bgood\b.*):?\s*$",
    re.IGNORECASE,
)
BAD_COMMENT_RE = re.compile(r"^#\s*Bad\b", re.IGNORECASE | re.MULTILINE)


class LintExpectation(str, Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class YamlBlock:
    source: Path
    line: int
    title: str
    expectation: LintExpectation
    content: str


def slugify(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(path))


def is_negative_title(title: str) -> bool:
    lowered = title.lower()
    return (
        lowered.startswith(".don't do this")
        or lowered.startswith(".don’t do this")
        or lowered.startswith(".instead of this")
        or lowered.startswith(".bad:")
        or lowered.startswith(".bad ")
        or bool(BAD_MARKER_RE.search(title))
        or bool(re.search(r"\bbad\b", title, re.IGNORECASE))
    )


def classify_block(lines: list[str], block_start: int, content: str) -> tuple[str, LintExpectation]:
    title = ""
    for index in range(block_start - 1, max(block_start - 8, -1), -1):
        stripped = lines[index].strip()
        if stripped.startswith("."):
            title = stripped
            break

    if title and is_negative_title(title):
        return title, LintExpectation.FAIL

    if title and POSITIVE_TITLE_RE.match(title):
        return title, LintExpectation.PASS

    if BAD_COMMENT_RE.search(content):
        return title, LintExpectation.FAIL

    stripped = content.strip()
    include_match = INCLUDE_RE.match(stripped)
    if include_match and BAD_MARKER_RE.search(include_match.group(1)):
        return title, LintExpectation.FAIL

    if title and re.search(r"\bgood\b", title, re.IGNORECASE):
        return title, LintExpectation.PASS

    return title, LintExpectation.PASS


def is_ansible_content(content: str) -> bool:
    if "ansible.builtin." in content or "ansible.legacy." in content:
        return True
    if re.search(
        r"^\s*(?:hosts|tasks|roles|collections|argument_specs|dependency|"
        r"provisioner|platforms)\s*:",
        content,
        re.MULTILINE,
    ):
        return True
    if re.search(r"^\s*controller_\w+\s*:", content, re.MULTILINE):
        return True
    return False


def resolve_content(adoc_path: Path, content: str) -> str:
    stripped = content.strip()
    match = INCLUDE_RE.match(stripped)
    if match and "\n" not in stripped:
        include_path = (adoc_path.parent / match.group(1)).resolve()
        if not include_path.is_file():
            msg = f"{adoc_path}: include target not found: {match.group(1)}"
            raise FileNotFoundError(msg)
        return include_path.read_text(encoding="utf-8")
    return content


def normalize_content(content: str) -> str:
    if not content.endswith("\n"):
        return content + "\n"
    return content


def extract_blocks(adoc_path: Path) -> list[YamlBlock]:
    lines = adoc_path.read_text(encoding="utf-8").splitlines()
    blocks: list[YamlBlock] = []
    index = 0

    while index < len(lines):
        if lines[index].strip() != "[source,yaml]":
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

        raw_content = "\n".join(content_lines)
        content = normalize_content(resolve_content(adoc_path, raw_content))
        if not is_ansible_content(content):
            index += 1
            continue
        title, expectation = classify_block(lines, block_start, raw_content)
        blocks.append(
            YamlBlock(
                source=adoc_path.resolve(),
                line=block_start,
                title=title,
                expectation=expectation,
                content=content,
            )
        )
        index += 1

    return blocks


def find_adoc_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix == ".adoc":
            files.append(path.resolve())
            continue
        if path.is_dir():
            files.extend(sorted(path.rglob("*.adoc")))
    return sorted({path for path in files})


def write_block(block: YamlBlock, output_dir: Path) -> Path:
    filename = f"{slugify(block.source)}_L{block.line}.yml"
    target = output_dir / filename
    target.write_text(block.content, encoding="utf-8")
    metadata = target.with_suffix(".json")
    metadata.write_text(json.dumps(asdict(block), default=str), encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract YAML blocks from AsciiDoc files.",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="AsciiDoc files or directories to scan",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory where extracted YAML files are written",
    )
    parser.add_argument(
        "--expectation",
        choices=[item.value for item in LintExpectation],
        help="Only extract blocks with this lint expectation",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print extracted YAML file paths to stdout",
    )
    parser.add_argument(
        "--manifest",
        action="store_true",
        help="Print JSON metadata for extracted blocks to stdout",
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    adoc_files = find_adoc_files(args.paths)
    extracted: list[Path] = []

    for adoc_path in adoc_files:
        for block in extract_blocks(adoc_path):
            if args.expectation and block.expectation.value != args.expectation:
                continue
            extracted.append(write_block(block, args.output_dir))

    if args.manifest:
        for yaml_path in extracted:
            metadata_path = yaml_path.with_suffix(".json")
            print(metadata_path.read_text(encoding="utf-8").strip())

    if args.list:
        for path in extracted:
            print(path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
