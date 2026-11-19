#!/bin/bash
# One-time setup on macOS: creates a virtualenv and installs dependencies.
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
[ -f config.yaml ] || cp config.example.yaml config.yaml
echo "Done. Edit config.yaml, add service-account.json, then: ./run.sh"
echo "Try it with fake data first:  ./run.sh --demo"
