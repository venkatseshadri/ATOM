#!/bin/bash
# ATOM EOD P&L notification — one-shot, cron-fired once daily after market close.
# Runs pnl_report.py for today and sends the output to Telegram via notify.py.
#
# Usage (cron, trading_ceo user — matches run_atom_paper.sh's ownership):
#   35 15 * * 1-5 /home/trading_ceo/atom/cron/notify_eod_pnl.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="/usr/bin/python3"
LOG_FILE="$PROJECT_DIR/logs/notify_eod_pnl_$(date +%Y%m%d).log"

mkdir -p "$PROJECT_DIR/logs"
cd "$PROJECT_DIR"

echo "----- $(date '+%F %T') -----" >> "$LOG_FILE"
REPORT="$("$PYTHON_BIN" pnl_report.py 2>&1)"
echo "$REPORT" >> "$LOG_FILE"
"$PYTHON_BIN" notify.py "$REPORT" >> "$LOG_FILE" 2>&1
