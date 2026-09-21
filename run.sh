#!/bin/bash
# Use .venv/bin/python directly rather than `activate`: activate hardcodes the
# venv's absolute path, so calling the interpreter keeps working if this folder
# ever moves again.
cd "$(dirname "$0")"
# Make sure ollama is up for cleanup.
if command -v ollama >/dev/null 2>&1 && ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
  nohup ollama serve >/tmp/ollama.log 2>&1 &
  sleep 2
fi
exec ./.venv/bin/python app.py
