#!/usr/bin/env bash
# Run the full test suite.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "Running tests..."
PYTHONPATH=src python3 -m pytest tests/ --tb=short "$@"
