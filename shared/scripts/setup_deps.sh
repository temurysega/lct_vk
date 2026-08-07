#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== BrandDeck AI: dependency setup ==="
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD=python
else
    echo "ERROR: Python 3.10+ is required." >&2
    exit 1
fi

"$PYTHON_CMD" --version
"$PYTHON_CMD" -m pip install -e "${PROJECT_ROOT}[api,documents]"
"$PYTHON_CMD" -m slide_agent --json doctor

echo "=== Setup complete ==="
echo "Node.js is optional and only required for the legacy PPTXGenJS runner."
