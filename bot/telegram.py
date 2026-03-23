"""Lightweight Telegram notifier for trading bot alerts."""

import logging
import os
import urllib.request
import json
from typing import Optional

logger = logging.getLogger(__name__)

_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def send_alert(message: str, silent: bool = False) -> bool:
    """Send a Telegram message. Non-blocking, never raises.

    Args:
        message: HTML-formatted message text (max 4096 chars).
        silent: If True, sends without triggering a notification sound.

    Returns:
        True if the message was sent successfully, False otherwise.
    """
    if not _BOT_TOKEN or not _CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
        payload = json.dumps({
            "chat_id": _CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": silent,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception as e:
        logger.debug("telegram_send_failed", extra={"error": str(e)})
        return False
