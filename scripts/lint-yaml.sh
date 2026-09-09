#!/usr/bin/env bash
# Lint YAML from files or stdin and verify it passes yamllint and ansible-lint.
#
# Usage:
#   scripts/lint-yaml.sh [OPTIONS] [FILE ...]
#   some-command | scripts/lint-yaml.sh
#   scripts/lint-yaml.sh -
#
# When no files are given and stdin is not a terminal, YAML is read from stdin.
# Exit 0 when all targets pass; exit 1 when any linter reports a failure.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
YAMLLINT_CONFIG="${REPO_ROOT}/.yamllint"
YAMLLINT_GHA_CONFIG="${REPO_ROOT}/scripts/yamllint-gha.yml"
ANSIBLE_LINT_CONFIG="${REPO_ROOT}/.ansible-lint"
EXTRACT_SCRIPT="${REPO_ROOT}/scripts/extract-adoc-yaml.py"

RUN_YAMLLINT=1
RUN_ANSIBLE_LINT=1
LINT_DOCS=0
TARGETS=()
FAILED=0

usage() {
  cat <<'EOF'
Lint YAML from files or stdin and verify it passes yamllint and ansible-lint.

Usage:
  scripts/lint-yaml.sh [OPTIONS] [FILE ...]
  some-command | scripts/lint-yaml.sh
  scripts/lint-yaml.sh -

Options:
  -h, --help           Show this help message
  --docs               Lint YAML embedded in AsciiDoc documentation
  --yamllint-only      Run only yamllint
  --ansible-lint-only  Run only ansible-lint
  --no-ansible-lint    Skip ansible-lint (alias for --yamllint-only)
  --no-yamllint        Skip yamllint (alias for --ansible-lint-only)

When no FILE arguments are given and stdin is not a terminal, YAML is read from
stdin. Use "-" to read from stdin explicitly.

Examples:
  scripts/lint-yaml.sh inventories/inventory_loop_hosts/playbook_good.yml
  scripts/lint-yaml.sh --docs
  ansible-playbook --syntax-check -o /dev/null playbook.yml 2>/dev/null \
    || scripts/lint-yaml.sh playbook.yml
  cat generated.yml | scripts/lint-yaml.sh
EOF
}

run_yamllint() {
  local config="$1"
  local path="$2"
  yamllint -c "$config" "$path"
}

lint_docs() {
  local tmpdir cleanup_path=""
  local yaml_path metadata_path label linter

  require_command python3
  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/lint-yaml-docs.XXXXXX")"
  cleanup_path="$tmpdir"

  if ! python3 "$EXTRACT_SCRIPT" --output-dir "$tmpdir" --expectation pass "$REPO_ROOT"; then
    rm -rf "$cleanup_path"
    return 1
  fi

  shopt -s nullglob
  for yaml_path in "$tmpdir"/*.yml; do
    metadata_path="${yaml_path%.yml}.json"
    if [[ -f "$metadata_path" ]]; then
      label="$(
        python3 - <<'PY' "$metadata_path"
import json
import sys
from pathlib import Path

metadata = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
source = Path(metadata["source"])
try:
    source = source.relative_to(Path.cwd())
except ValueError:
    pass
title = metadata.get("title") or "(untitled example)"
print(f"{source}:{metadata['line']} {title}")
PY
      )"
      linter="$(
        python3 - <<'PY' "$metadata_path"
import json
import sys
from pathlib import Path

metadata = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(metadata.get("linter", "ansible"))
PY
      )"
    else
      label="$yaml_path"
      linter="ansible"
    fi

    if [[ "$RUN_YAMLLINT" -eq 1 ]]; then
      echo "==> yamllint: ${label}"
      if [[ "$linter" == "yamllint-gha" ]]; then
        if ! run_yamllint "$YAMLLINT_GHA_CONFIG" "$yaml_path"; then
          FAILED=1
        fi
      elif ! run_yamllint "$YAMLLINT_CONFIG" "$yaml_path"; then
        FAILED=1
      fi
    fi

    if [[ "$RUN_ANSIBLE_LINT" -eq 1 && "$linter" == "ansible" ]]; then
      echo "==> ansible-lint: ${label}"
      if ! ansible-lint -c "$ANSIBLE_LINT_CONFIG" "$yaml_path"; then
        FAILED=1
      fi
    fi
  done
  shopt -u nullglob

  rm -rf "$cleanup_path"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: required command not found: $1" >&2
    exit 127
  fi
}

lint_target() {
  local label="$1"
  local path="$2"
  local rc=0
  local cleanup_path=""

  if [[ "$path" == "-" ]]; then
    cleanup_path="$(mktemp "${TMPDIR:-/tmp}/lint-yaml.XXXXXX")"
    cat >"$cleanup_path"
    path="$cleanup_path"
    label="stdin"
  fi

  if [[ "$RUN_YAMLLINT" -eq 1 ]]; then
    echo "==> yamllint: ${label}"
    if ! run_yamllint "$YAMLLINT_CONFIG" "$path"; then
      rc=1
    fi
  fi

  if [[ "$RUN_ANSIBLE_LINT" -eq 1 ]]; then
    echo "==> ansible-lint: ${label}"
    if ! ansible-lint -c "$ANSIBLE_LINT_CONFIG" "$path"; then
      rc=1
    fi
  fi

  if [[ -n "$cleanup_path" ]]; then
    rm -f "$cleanup_path"
  fi

  return "$rc"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --docs)
      LINT_DOCS=1
      ;;
    --yamllint-only|--no-ansible-lint)
      RUN_ANSIBLE_LINT=0
      ;;
    --ansible-lint-only|--no-yamllint)
      RUN_YAMLLINT=0
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        TARGETS+=("$1")
        shift
      done
      break
      ;;
    -*)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      TARGETS+=("$1")
      ;;
  esac
  shift
done

if [[ "$RUN_YAMLLINT" -eq 1 ]]; then
  require_command yamllint
fi
if [[ "$RUN_ANSIBLE_LINT" -eq 1 ]]; then
  require_command ansible-lint
fi

if [[ "$LINT_DOCS" -eq 1 ]]; then
  lint_docs
  if [[ "$FAILED" -eq 1 ]]; then
    echo "lint-yaml: one or more documentation examples failed" >&2
    exit 1
  fi
  echo "lint-yaml: all documentation examples passed"
  exit 0
fi

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  if [[ -t 0 && -t 1 ]]; then
    echo "error: no input files given and stdin is a terminal" >&2
    usage >&2
    exit 2
  fi
  TARGETS=("-")
fi

for target in "${TARGETS[@]}"; do
  if [[ "$target" == "-" ]]; then
    if ! lint_target "stdin" "-"; then
      FAILED=1
    fi
    continue
  fi

  if [[ ! -e "$target" ]]; then
    echo "error: file not found: $target" >&2
    exit 2
  fi

  if ! lint_target "$target" "$target"; then
    FAILED=1
  fi
done

if [[ "$FAILED" -eq 1 ]]; then
  echo "lint-yaml: one or more targets failed" >&2
  exit 1
fi

echo "lint-yaml: all targets passed"
