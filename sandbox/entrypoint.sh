#!/bin/sh
# Usage: entrypoint.sh <target_file>
# Applies /in/patch.diff and runs the unit suite

set -e
cd /repo
git apply /in/patch.diff 2>&1 || { echo "PATCH_APPLY_FAILED"; exit 1; }
pytest -q tests/ -m "not docker" \
  --ignore=tests/test_mcp_retrieval_server.py \
  --ignore=tests/test_retrieval_search.py 2>&1
