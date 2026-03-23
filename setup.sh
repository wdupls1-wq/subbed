#!/usr/bin/env bash
# SubStation — one-time setup script
# Run this once from the project directory before first launch.
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"

echo "╔══════════════════════════════════════════════╗"
echo "║         SubStation — Setup                  ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── Python version check ────────────────────────────────────────────────────
PYTHON=$(command -v python3.11 || command -v python3.12 || command -v python3 || true)
if [ -z "$PYTHON" ]; then
    echo "❌  Python 3.11+ not found. Install via: brew install python@3.11"
    exit 1
fi

PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR="${PY_VER%%.*}"
PY_MINOR="${PY_VER#*.}"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "❌  Python $PY_VER is too old. Need 3.10+. Found: $PYTHON"
    exit 1
fi
echo "✅  Python $PY_VER  ($PYTHON)"

# ── Virtual environment ─────────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "   Creating virtual environment…"
    "$PYTHON" -m venv "$VENV_DIR"
fi

# Upgrade pip silently
"$VENV_DIR/bin/pip" install --upgrade pip --quiet

# Install dependencies
echo "   Installing Python packages (this may take a few minutes on first run)…"
"$VENV_DIR/bin/pip" install --quiet -r "$PROJECT_DIR/requirements.txt"
echo "✅  Python packages installed"

# ── ffmpeg check ────────────────────────────────────────────────────────────
FFMPEG_PATHS=(
    "/opt/homebrew/bin/ffmpeg"
    "/usr/local/bin/ffmpeg"
    "/usr/bin/ffmpeg"
)
FFMPEG_FOUND=""
for p in "${FFMPEG_PATHS[@]}"; do
    if [ -x "$p" ]; then
        FFMPEG_FOUND="$p"
        break
    fi
done
if [ -z "$FFMPEG_FOUND" ]; then
    FFMPEG_FOUND=$(command -v ffmpeg || true)
fi

if [ -z "$FFMPEG_FOUND" ]; then
    echo ""
    echo "⚠️   ffmpeg not found."
    echo "    Install with:  brew install ffmpeg"
    echo "    SubStation will not be able to extract audio without it."
    echo ""
else
    echo "✅  ffmpeg found at: $FFMPEG_FOUND"
fi

# ── Output folder ───────────────────────────────────────────────────────────
OUTPUT_DIR="$HOME/Desktop/SubStation Output"
mkdir -p "$OUTPUT_DIR"
echo "✅  Output folder: $OUTPUT_DIR"

# ── Make launcher executable ────────────────────────────────────────────────
chmod +x "$PROJECT_DIR/SubStation.app/Contents/MacOS/launcher" 2>/dev/null || true

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Setup complete!                           ║"
echo "║                                             ║"
echo "║   Launch:  double-click SubStation.app      ║"
echo "║   Or run:  ./SubStation.app/Contents/MacOS/launcher"
echo "╚══════════════════════════════════════════════╝"
