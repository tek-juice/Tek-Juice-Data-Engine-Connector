#!/usr/bin/env bash
# Build the distribution packages (sdist + wheel).
# Requires: pip install build
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

if ! python3 -m build --version &>/dev/null; then
    echo "build not found. Installing..."
    pip install build
fi

echo "Building distribution packages..."
python3 -m build

echo ""
echo "Packages written to dist/:"
ls -lh dist/
