#!/bin/bash
# ATOM hourly Telegram update — same digest as notify_eod_pnl.sh, fired during market
# hours for intraday visibility instead of waiting for the 15:35 EOD summary.
#
# Usage: registered under ROOT's crontab, matching run_atom_paper.sh's actual (not
# documented) deployment — see that script's header for why.
#   0 10-15 * * 1-5 /home/trading_ceo/atom/cron/notify_hourly_report.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="/usr/bin/python3"
LOG_FILE="$PROJECT_DIR/logs/notify_hourly_$(date +%Y%m%d).log"

mkdir -p "$PROJECT_DIR/logs"
cd "$PROJECT_DIR"

echo "----- $(date '+%F %T') -----" >> "$LOG_FILE"
REPORT="$("$PYTHON_BIN" atom_report.py 2>&1)"
echo "$REPORT" >> "$LOG_FILE"
"$PYTHON_BIN" notify.py "$REPORT" >> "$LOG_FILE" 2>&1
