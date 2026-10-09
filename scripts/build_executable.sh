#!/usr/bin/env bash
# Build a standalone macOS binary and package it for sharing.
# Env: PYTHON (interpreter to use), PYINSTALLER_VERSION (pinned below by default).
set -euo pipefail
cd "$(dirname "$0")/.."

PYINSTALLER_VERSION=${PYINSTALLER_VERSION:-6.22.3}

# Pick an interpreter: $PYTHON, else .venv, else create .venv (uv if present, else python3).
if [ -n "${PYTHON:-}" ]; then
  PY=$PYTHON
else
  PY=.venv/bin/python
  if [ ! -x "$PY" ]; then
    echo "creating .venv ..." >&2
    if command -v uv >/dev/null 2>&1; then
      uv venv .venv >/dev/null
    else
      python3 -m venv .venv
    fi
  fi
fi

# uv-created venvs ship without pip, so bootstrap it when missing.
"$PY" -m pip --version >/dev/null 2>&1 || "$PY" -m ensurepip --upgrade >/dev/null
"$PY" -m pip install --quiet -e . "pyinstaller==$PYINSTALLER_VERSION"

VERSION=$("$PY" -c 'from reclaim import __version__; print(__version__)')
ARCH=$(uname -m)
NAME="reclaim-$VERSION-macos-$ARCH"

rm -rf dist/reclaim "dist/$NAME" "dist/$NAME.zip" "dist/$NAME.zip.sha256"
mkdir -p build
cat > build/_entry.py <<'PYEOF'
from reclaim.cli import main

raise SystemExit(main())
PYEOF
"$PY" -m PyInstaller \
  --onefile \
  --name reclaim \
  --paths src \
  --add-data "$(pwd)/src/reclaim/catalog_data:reclaim/catalog_data" \
  --add-data "$(pwd)/src/reclaim/tui.tcss:reclaim" \
  --collect-all textual \
  --collect-all rich \
  --distpath dist \
  --workpath build/pyinstaller \
  --specpath build \
  --noconfirm \
  --clean \
  build/_entry.py

# Smoke test against an empty HOME so the result doesn't depend on this machine's disk.
# --version proves the bundle starts; --scan-only --json proves the catalog data is
# bundled (load_catalog runs) and the scanner emits valid JSON.
SMOKE_HOME=$(mktemp -d)
trap 'rm -rf "$SMOKE_HOME"' EXIT
fail() { echo "smoke test failed: $1" >&2; exit 1; }

GOT=$(HOME="$SMOKE_HOME" ./dist/reclaim --version) || fail "--version did not run"
[ "$GOT" = "reclaim $VERSION" ] || fail "version mismatch: got '$GOT', want 'reclaim $VERSION'"
HOME="$SMOKE_HOME" ./dist/reclaim --scan-only --json \
  | "$PY" -c 'import json,sys; d=json.load(sys.stdin); assert d["version"]==1 and "items" in d' \
  || fail "--scan-only --json did not produce valid output"

# Package: zip keeps the exec bit; sha256 lets recipients verify the download.
mkdir -p "dist/$NAME"
cp dist/reclaim "dist/$NAME/reclaim"
(cd dist && zip -qr "$NAME.zip" "$NAME" && shasum -a 256 "$NAME.zip" > "$NAME.zip.sha256")
rm -rf "dist/$NAME"

echo "built:    $(pwd)/dist/reclaim"
echo "package:  $(pwd)/dist/$NAME.zip"
echo "checksum: $(cat "dist/$NAME.zip.sha256")"
echo "note: binary is ad-hoc signed; recipients may need: xattr -d com.apple.quarantine ./reclaim" >&2
