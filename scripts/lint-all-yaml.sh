#!/usr/bin/env bash
# Lint standalone Ansible YAML and YAML embedded in AsciiDoc documentation.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LINT_SCRIPT="${REPO_ROOT}/scripts/lint-yaml.sh"
YAML_LINT_DIRS=(playbooks roles inventories)

cd "$REPO_ROOT"

while IFS= read -r -d '' yaml_file; do
  "$LINT_SCRIPT" "$yaml_file"
done < <(
  find "${YAML_LINT_DIRS[@]}" -type f \( -name '*.yml' -o -name '*.yaml' \) \
    -print0 2>/dev/null | sort -z
)

"$LINT_SCRIPT" --docs
