#!/bin/bash
# LocalFlow setup — creates a venv, installs deps, ensures Ollama + models.
set -e
cd "$(dirname "$0")"
echo "==> LocalFlow setup"

# 1. Python venv (must be native arm64 for MLX — NOT anaconda/Intel)
PY="$(command -v /opt/homebrew/bin/python3.11 || command -v /opt/homebrew/bin/python3 || command -v python3)"
if [ ! -d ".venv" ]; then
  echo "==> Creating virtualenv (.venv) with $PY"
  "$PY" -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
echo "==> Installing Python deps"
pip install -r requirements.txt

# 2. Ollama (for AI cleanup)
if ! command -v ollama >/dev/null 2>&1; then
  echo "==> Installing Ollama via Homebrew"
  brew install ollama || echo "   (install Ollama manually from https://ollama.com if this fails)"
fi

if command -v ollama >/dev/null 2>&1; then
  # Start the server if it isn't already up.
  if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "==> Starting ollama serve (background)"
    nohup ollama serve >/tmp/ollama.log 2>&1 &
    sleep 3
  fi
  echo "==> Pulling cleanup model (llama3.2:3b)"
  ollama pull llama3.2:3b || true
fi

echo ""
echo "==> Done. Run it with:  ./run.sh"
echo "    First run downloads the Whisper model (~1.5GB) — be patient."
echo "    Grant Microphone + Accessibility + Input Monitoring when macOS asks."
