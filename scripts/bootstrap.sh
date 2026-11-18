#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install --index-url https://pypi.org/simple --require-hashes -r requirements/linux-aarch64.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
git config core.hooksPath .githooks
