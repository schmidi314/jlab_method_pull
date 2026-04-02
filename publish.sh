#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Publish jlab_method_pull to PyPI
# Usage: ./publish.sh
# Requires: PYPI_TOKEN env var, or will prompt for it.
# ---------------------------------------------------------------------------

cd "$(dirname "$0")"

# Clean previous build artifacts
rm -rf dist/ build/ *.egg-info/

# Build
echo "Building..."
python -m build

# Upload
echo "Uploading to PyPI..."
twine upload dist/* 

echo "Done."
