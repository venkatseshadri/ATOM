#!/bin/bash
# ATOM Phase 1/2 — paper-only live cycle (one-shot, cron-fired per minute).
# Index picked by day-of-week 0-1 DTE rule (run_live_once.py): NIFTY Fri/Mon/Tue,
# SENSEX Wed/Thu. Reads that day's Penguin capture_{index}.sqlite READ-ONLY, writes
# only atom/data/atom_state{,_sensex}.sqlite (separate FSM/position state per index).
# NO broker, NO credentials (Phase 3 = broker routing). flock = one cycle at a time.
#
# Usage: registered under ROOT's crontab (not trading_ceo — there is no trading_ceo
# crontab on this box). atom_state.sqlite/logs/data end up root-owned as a result.
# Originally documented/intended to run as trading_ceo; never actually deployed that
# way. Revisit ownership deliberately later — not fixed here, just describing reality.
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
