#!/usr/bin/env bash
# Local MacBook Pro (M5 / arm64) setup.
#
# The Mac's job: learn Docker concepts, read code, edit over Remote-SSH, and
# explore ESM-2 natively on MPS. It is NOT where the project container is built
# or where any anchor number is produced.

set -euo pipefail

command -v brew >/dev/null 2>&1 || {
  echo "Install Homebrew first: https://brew.sh"; exit 1; }

echo "==> CLI tooling"
brew install git jq just direnv awscli uv rsync tmux gnupg

echo "==> Container runtime (for concept practice only)"
# OrbStack is lighter and faster than Docker Desktop on Apple Silicon and has no
# per-seat licence question. Either works; colima is the fully-FOSS option.
brew install --cask orbstack || echo "   (or: brew install colima docker docker-buildx)"

echo "==> pre-commit + secret scanning"
brew install pre-commit gitleaks

echo "==> Local python env for MPS exploration"
uv venv --python 3.11 .venv-local
# shellcheck disable=SC1091
source .venv-local/bin/activate
uv pip install -r requirements-local.txt
uv pip install -e . --no-deps

echo
echo "Done. Sanity check MPS:"
echo "  source .venv-local/bin/activate"
echo "  python -c \"import torch; print(torch.backends.mps.is_available())\""
echo
echo "Then verify the host:  ./scripts/doctor.sh"
