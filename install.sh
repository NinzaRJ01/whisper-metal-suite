#!/usr/bin/env bash
# ==============================================================================
# install.sh: Standalone 1-Click Installer for Whisper Metal Suite (Apple Silicon)
# ==============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
MODELS_DIR="$HOME/.whisper-models"
MEETINGS_DIR="$HOME/EmptyOne/meetings"

echo "======================================================================"
echo " 🎙️  Whisper Metal Suite: Apple Silicon Speech & Meeting Intelligence"
echo "======================================================================"

# 1. Architecture Verification
ARCH=$(uname -m)
if [[ "$ARCH" != "arm64" ]]; then
    echo "❌ Error: Whisper Metal Suite is optimized for Apple Silicon (M1/M2/M3/M4/M5)." >&2
    echo "Current architecture: $ARCH" >&2
    exit 1
fi
echo "✓ Architecture: Apple Silicon ($ARCH)"

# 2. Check Prerequisites
if ! command -v brew >/dev/null 2>&1; then
    echo "❌ Error: Homebrew is required. Install from https://brew.sh" >&2
    exit 1
fi

echo "==> Ensuring whisper.cpp and ffmpeg are installed via Homebrew..."
brew install whisper.cpp ffmpeg

# 3. Create Necessary Directories
mkdir -p "$BIN_DIR" "$MODELS_DIR" "$MEETINGS_DIR"

# 4. Download Optimized English Models
echo "==> Checking Whisper Metal models in $MODELS_DIR..."

BASE_EN="$MODELS_DIR/ggml-base.en.bin"
if [[ ! -f "$BASE_EN" ]]; then
    echo "--> Downloading ggml-base.en.bin (~148 MB) for instant live streaming..."
    curl -L --fail -C - -o "$BASE_EN" \
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"
else
    echo "✓ Found ggml-base.en.bin"
fi

TURBO="$MODELS_DIR/ggml-large-v3-turbo.bin"
if [[ ! -f "$TURBO" ]]; then
    echo "--> Downloading ggml-large-v3-turbo.bin (~1.6 GB) for high-accuracy meetings..."
    curl -L --fail -C - -o "$TURBO" \
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin"
else
    echo "✓ Found ggml-large-v3-turbo.bin"
fi

# 5. Compile Swift Daemons
echo "==> Compiling native Swift daemons with Metal acceleration..."

# 5a. Dictation Pill
swiftc -O "$REPO_DIR/src/dictation/whisper_pill.swift" -o "$BIN_DIR/whisper-pill"
cp "$REPO_DIR/src/dictation/whisper-daemon" "$BIN_DIR/whisper-daemon"
chmod +x "$BIN_DIR/whisper-pill" "$BIN_DIR/whisper-daemon"
echo "✓ Compiled: $BIN_DIR/whisper-pill"

# 5b. Meeting Recorder
swiftc -O "$REPO_DIR/src/meetings/meeting_recorder.swift" -o "$BIN_DIR/meeting-recorder"
cp "$REPO_DIR/src/meetings/meeting-record" "$BIN_DIR/meeting-record"
chmod +x "$BIN_DIR/meeting-recorder" "$BIN_DIR/meeting-record"
echo "✓ Compiled: $BIN_DIR/meeting-recorder"

# 6. Copy Python Synthesis & Pruning Engines
mkdir -p "$HOME/EmptyOne"
cp "$REPO_DIR/src/meetings/summarize_meeting.py" "$HOME/EmptyOne/summarize_meeting.py"
cp "$REPO_DIR/src/meetings/prune_meetings.py" "$HOME/EmptyOne/prune_meetings.py"
chmod +x "$HOME/EmptyOne/summarize_meeting.py" "$HOME/EmptyOne/prune_meetings.py"

# 7. Start Services
echo "==> Starting background daemons..."
"$BIN_DIR/whisper-daemon" restart || true
"$BIN_DIR/meeting-record" stop >/dev/null 2>&1 || true
sleep 0.5
"$BIN_DIR/meeting-record" start || true

echo ""
echo "======================================================================"
echo " 🎉 Installation Complete! Whisper Metal Suite is Active."
echo "======================================================================"
echo "👉 Push-to-Talk Dictation: Hold 'Right Option' (⌥) to speak, release to paste"
echo "👉 Meeting Recorder:       Double-tap Option (⌥ ⌥) or click 🎙️ in Menu Bar"
echo "👉 Stealth Screen-Share:   'meeting-record start --stealth' (invisible to Zoom/Meet)"
echo "👉 Local Privacy Shield:   All passwords/keys redacted to variables before cloud"
echo "👉 View Architecture:      open $REPO_DIR/web/index.html"
echo "======================================================================"
