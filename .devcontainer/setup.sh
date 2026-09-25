#!/usr/bin/env bash
# Runs once when the Codespace is created: the app's Python packages and Claude Code.
set -e
cd "$(dirname "$0")/.."
pip install --user -q -r requirements.txt
command -v claude >/dev/null || npm install -g @anthropic-ai/claude-code
git config pull.rebase false
