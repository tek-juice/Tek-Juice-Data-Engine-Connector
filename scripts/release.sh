#!/usr/bin/env bash
# Release checklist script — does NOT publish automatically.
#
# This script verifies that the project is in a releasable state and
# prints the steps needed to publish. It never runs `twine upload`
# or `git push` automatically.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "=== Tek Juice Connector release checklist ==="
echo ""

# 1. Check version consistency
VERSION=$(python3 -c "
import sys
sys.path.insert(0, 'src')
from tekjuice_connector.version import __version__
print(__version__)
")
echo "Package version: $VERSION"

TOML_VERSION=$(grep '^version = ' pyproject.toml | head -1 | sed 's/version = "//;s/"//')
if [ "$VERSION" != "$TOML_VERSION" ]; then
    echo "ERROR: version.py ($VERSION) does not match pyproject.toml ($TOML_VERSION)"
    exit 1
fi
echo "[OK] Version consistent: $VERSION"

# 2. Run tests
echo ""
echo "Running tests..."
PYTHONPATH=src python3 -m pytest tests/ --tb=short -q
echo "[OK] All tests passed"

# 3. Check no real credentials in repo
echo ""
echo "Checking for accidental credentials..."
if grep -r "TEKJUICE_CONNECTOR_KEY=" src/ tests/ examples/ --include="*.py" | grep -v "placeholder\|your-\|example\|test-" | grep -v "^Binary"; then
    echo "WARNING: Possible credential found — review before publishing."
else
    echo "[OK] No real credentials detected"
fi

# 4. Check .env not tracked
if git ls-files --error-unmatch .env &>/dev/null 2>&1; then
    echo "ERROR: .env is tracked by git — remove it immediately."
    exit 1
fi
echo "[OK] .env not tracked by git"

echo ""
echo "=== All checks passed ==="
echo ""
echo "To publish to PyPI:"
echo "  1. git tag v$VERSION"
echo "  2. git push origin v$VERSION"
echo "  3. bash scripts/build.sh"
echo "  4. twine upload dist/tekjuice_connector-${VERSION}*"
echo ""
echo "Do NOT publish automatically. Review dist/ contents first."
