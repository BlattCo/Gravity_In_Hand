#!/usr/bin/env bash
# Gravity In Hand (BlattCo) — Launcher Script
# Strictly enforces Directive 1 (Background Process Naming Protocol):
# [4-char App Name]_[4-char Component]_[MMDDHHMM].py -> GRAV_DECK_MMDDHHMM.py
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP="$(date +"%m%d%H%M")"
PROC_NAME="GRAV_DECK_${TIMESTAMP}.py"

ln -sf "${SCRIPT_DIR}/src/gravity_gui.py" "/tmp/${PROC_NAME}"

export PYTHONPATH="${SCRIPT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
echo "[INFO] Starting Gravity In Hand Deck as process: ${PROC_NAME}"
python3 "/tmp/${PROC_NAME}" &
