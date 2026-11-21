#!/bin/sh
# Install the cp4ai CLI and extract the bundled helpers for this machine.
#
# Usage (from a checkout):
#   ./install.sh
#
# Requires uv: https://docs.astral.sh/uv/
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if ! command -v uv >/dev/null 2>&1; then
    echo "error: uv is required; install it from https://docs.astral.sh/uv/" >&2
    exit 1
fi

uv tool install --force "$ROOT"

BIN_DIR=$(uv tool dir --bin 2>/dev/null || true)
if [ -n "${BIN_DIR:-}" ] && [ -x "$BIN_DIR/cp4ai-setup" ]; then
    "$BIN_DIR/cp4ai-setup"
elif command -v cp4ai-setup >/dev/null 2>&1; then
    cp4ai-setup
else
    echo "Installed. The helpers will be extracted on first use." >&2
fi
