#!/usr/bin/env bash
# Lint Ansible YAML under known example and content directories.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ANSIBLE_LINT_CONFIG="${REPO_ROOT}/.ansible-lint"
LINT_DIRS=(playbooks roles inventories examples)
FAILED=0

cd "$REPO_ROOT"

if ! command -v ansible-lint >/dev/null 2>&1; then
  echo "error: required command not found: ansible-lint" >&2
  exit 127
fi

existing_dirs=()
for dir in "${LINT_DIRS[@]}"; do
  if [[ -d "$dir" ]]; then
    existing_dirs+=("$dir")
  fi
done

if [[ ${#existing_dirs[@]} -eq 0 ]]; then
  echo "lint-yaml: no lint directories found" >&2
  exit 1
fi

echo "==> ansible-lint: ${existing_dirs[*]}"
if ! ansible-lint -c "$ANSIBLE_LINT_CONFIG" --exclude .github/ "${existing_dirs[@]}"; then
  FAILED=1
fi

if [[ "$FAILED" -eq 1 ]]; then
  echo "lint-yaml: ansible-lint reported failures" >&2
  exit 1
fi

echo "lint-yaml: all targets passed"
