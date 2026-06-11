"""
Pushover push notifications – alerts Davide when human attention is needed.
"""

import requests
import threading

from dp_connect_bot.config import PUSHOVER_USER_KEY, PUSHOVER_API_TOKEN, log

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


def send_pushover(title, message, priority=0, url=None, url_title=None):
    """Send a Pushover push notification (non-blocking).

    Args:
        title: Notification title (max 250 chars)
        message: Notification body (max 1024 chars)
        priority: -2 (lowest) to 2 (emergency). Default 0 (normal).
        url: Optional clickable URL
        url_title: Optional label for the URL
    """
    if not PUSHOVER_USER_KEY or not PUSHOVER_API_TOKEN:
        log.warning("[pushover] Nicht konfiguriert (keys fehlen)")
        return

    def _send():
        try:
            data = {
                "token": PUSHOVER_API_TOKEN,
                "user": PUSHOVER_USER_KEY,
                "title": title[:250],
                "message": message[:1024],
                "priority": priority,
            }
            if url:
                data["url"] = url[:512]
            if url_title:
                data["url_title"] = url_title[:100]

            resp = requests.post(PUSHOVER_URL, data=data, timeout=10)
            if resp.ok:
                log.info(f"[pushover] Notification sent: {title}")
            else:
                log.error(f"[pushover] Failed ({resp.status_code}): {resp.text}")
        except Exception as e:
            log.error(f"[pushover] Error: {e}")

    # Send in background thread to not block the response
    threading.Thread(target=_send, daemon=True).start()


def notify_escalation(chat_id, channel, reason, collected_info, customer_name=None):
    """Send escalation notification to Davide.

    Called when the bot hands off a conversation to a human.
    """
    ch_label = {"telegram": "Telegram", "whatsapp": "WhatsApp", "web": "Webchat"}.get(channel, channel)
    name = customer_name or chat_id[:16]

    title = f"Support-Eskalation ({ch_label})"

    lines = [f"Kunde: {name}"]
    if reason:
        lines.append(f"Grund: {reason}")
    if collected_info:
        lines.append(f"\n{collected_info}")

    message = "\n".join(lines)

    send_pushover(
        title=title,
        message=message,
        priority=1,  # High priority – plays sound even in quiet hours
    )
