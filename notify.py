#!/usr/bin/env python3
"""Telegram notifier — direct Bot API call, no dependency on this box's private
automation stack (deliberately, to keep ATOM a standalone repo per docs/PORCUPINE.md's
own stated reasoning). Reads TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from the environment
or atom/.env (gitignored, never committed).
"""
import json
import os
import urllib.request
from pathlib import Path

_ENV_FILE = Path(__file__).parent / ".env"


def _env(key: str) -> str:
    val = os.environ.get(key)
    if val:
        return val
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return ""


def send_telegram(text: str) -> bool:
    """Send a Telegram message. Returns True only when actually delivered."""
    token = _env("TELEGRAM_BOT_TOKEN")
    chat_id = _env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp).get("ok", False)
    except Exception:
        return False


if __name__ == "__main__":
    import sys

    msg = " ".join(sys.argv[1:]) or "ATOM notify.py test ping"
    print("sent" if send_telegram(msg) else "FAILED (token/chat_id/network)")
