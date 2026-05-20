#!/usr/bin/env bash
# Verify the vendored copy under custom_components/wattbox/wattbox_local/
# is byte-identical to src/wattbox_local/.
#
# Fails (exit 1) if they differ — that means someone edited src/ without
# running scripts/sync_lib.sh and the HACS-installed integration would
# ship a stale library.
#
# Used by .pre-commit-config.yaml and CI.
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="src/wattbox_local"
DST="custom_components/wattbox/wattbox_local"

if [ ! -d "$SRC" ] || [ ! -d "$DST" ]; then
  echo "missing $SRC or $DST" >&2
  exit 1
fi

# Exclude __pycache__ — generated locally, never tracked.
if ! diff -r -x __pycache__ -x '*.pyc' "$SRC" "$DST" > /dev/null; then
  echo "vendored library is out of sync with src/" >&2
  echo "run: bash scripts/sync_lib.sh && git add $DST" >&2
  diff -r -x __pycache__ -x '*.pyc' "$SRC" "$DST" >&2 || true
  exit 1
fi
