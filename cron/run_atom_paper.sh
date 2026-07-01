#!/bin/bash
# ATOM Phase 1 — paper-only live cycle (one-shot, cron-fired per minute).
# Reads Penguin capture_nifty.sqlite READ-ONLY, writes only atom/data/atom_state.sqlite.
# NO broker, NO credentials (Phase 3 = broker routing). flock = one cycle at a time.
#
# Usage (cron, trading_ceo user — NOT root, so atom_state stays trading_ceo-owned):
#   */1 9-15 * * 1-5 /home/trading_ceo/atom/cron/run_atom_paper.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="/usr/bin/python3"
LOG_FILE="$PROJECT_DIR/logs/atom_paper_$(date +%Y%m%d).log"
LOCK_FILE="$PROJECT_DIR/locks/atom_paper.lock"

mkdir -p "$PROJECT_DIR/logs" "$(dirname "$LOCK_FILE")"

exec {LOCK_FD}>"$LOCK_FILE"
if ! flock -n "$LOCK_FD"; then
    exit 0                       # previous cycle still running — skip this tick
fi

cd "$PROJECT_DIR"                 # run_live_once.py uses repo-relative paths
echo "----- $(date '+%F %T') -----" >> "$LOG_FILE"
"$PYTHON_BIN" run_live_once.py >> "$LOG_FILE" 2>&1
