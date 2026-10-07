#!/usr/bin/env bash
# Run linting checks.
# Requires: pip install ruff  (or adjust to your preferred linter)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

if command -v ruff &>/dev/null; then
    echo "Running ruff..."
    ruff check src/ tests/
else
    echo "ruff not found. Install with: pip install ruff"
    echo "Falling back to py_compile check..."
    PYTHONPATH=src python3 -m py_compile \
        $(find src/ -name "*.py" | tr '\n' ' ')
    echo "Syntax check passed."
fi
