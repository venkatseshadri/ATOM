#!/bin/bash
# ATOM EOD notification — one-shot, cron-fired once daily after market close.
# Runs atom_report.py (status + P&L digest, PENGUIN/KALKI-style) and sends it to
# Telegram via notify.py. For a detailed per-trade ledger instead, run
# `python3 pnl_report.py` by hand — this cron sends the terse digest, not that.
#
# Usage: registered under ROOT's crontab, matching run_atom_paper.sh's actual (not
# documented) deployment — see that script's header for why.
#   35 15 * * 1-5 /home/trading_ceo/atom/cron/notify_eod_pnl.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="/usr/bin/python3"
LOG_FILE="$PROJECT_DIR/logs/notify_eod_pnl_$(date +%Y%m%d).log"

mkdir -p "$PROJECT_DIR/logs"
cd "$PROJECT_DIR"

echo "----- $(date '+%F %T') -----" >> "$LOG_FILE"
REPORT="$("$PYTHON_BIN" atom_report.py 2>&1)"
echo "$REPORT" >> "$LOG_FILE"
"$PYTHON_BIN" notify.py "$REPORT" >> "$LOG_FILE" 2>&1
