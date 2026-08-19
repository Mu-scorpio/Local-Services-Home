#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Allow an explicit interpreter, otherwise prefer a Python 3.10+.
if [ -n "${PYTHON:-}" ]; then
    PY="$PYTHON"
else
    PY=""
    for candidate in python3.11 python3.12 python3.13 python3.10 python3.14 python3; do
        for base in "" /opt/homebrew/bin /usr/local/bin; do
            if [ -n "$base" ]; then
                p="$base/$candidate"
            else
                p="$candidate"
            fi
            if command -v "$p" >/dev/null 2>&1 || [ -x "$p" ]; then
                if "$p" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
                    PY="$p"
                    break 2
                fi
            fi
        done
    done
fi

if [ -z "$PY" ] || ! command -v "$PY" >/dev/null 2>&1; then
    echo "[ERROR] Python 3.10+ not found. Install Python 3.10+ or set PYTHON=/path/to/python3.11." >&2
    exit 1
fi

echo "Using Python: $PY"
echo "============================================"
echo "  Local Services Home - Build macOS .app"
echo "============================================"
echo

echo "[1/3] Installing build dependencies..."
"$PY" -m pip install -q pyinstaller || "$PY" -m pip install --break-system-packages -q pyinstaller
"$PY" -m pip install -q -r requirements.txt || "$PY" -m pip install --break-system-packages -q -r requirements.txt

echo "[2/3] Cleaning old build output..."
rm -rf build dist

echo "[3/3] Packaging with PyInstaller (may take a few minutes)..."
"$PY" -m PyInstaller build_mac.spec --noconfirm --clean

# Keep only the .app bundle; the sibling COLLECT folder is an intermediate.
rm -rf "dist/本地服务管理"

echo
echo "============================================"
echo "  Build OK"
echo "  Output: dist/本地服务管理.app"
echo "============================================"
echo
echo "Copy the .app to /Applications or run it directly."
echo "User data: ~/Library/Application Support/Local Services Home"
