#!/bin/sh
# Usage: entrypoint.sh <target_file>
# Applies /in/patch.diff and runs pytest

set -e
cd /repo
git apply /in/patch.diff 2>&1 || { echo "PATCH_APPLY_FAILED"; exit 1; }
pytest -q tests/ 2>&1
