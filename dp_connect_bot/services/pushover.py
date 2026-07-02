"""
Pushover push notifications – alerts Davide when human attention is needed.
"""

import os
import time
import requests
import threading

from dp_connect_bot.config import PUSHOVER_USER_KEY, PUSHOVER_API_TOKEN, log, _BASE_DIR

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"

# Drosselung fuer den AI-Ausfall-Alarm: hoechstens 1 Push pro 30 Min, sonst
# wuerde jeder fehlgeschlagene API-Call (= jede Kundennachricht) eine Push ausloesen.
_OUTAGE_THROTTLE_FILE = os.path.join(_BASE_DIR, ".api_outage_alert.ts")
_OUTAGE_THROTTLE_S = 1800


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

    Called when the bot hands off a conversation to a human (Support-Eskalation
    ODER Rückruf-/Kontakt-Wunsch). Enthaelt die Telefonnummer + einen Ein-Tipp-Link,
    damit Davide direkt aus der Push heraus antworten/zurueckrufen kann.
    """
    ch_label = {"telegram": "Telegram", "whatsapp": "WhatsApp", "web": "Webchat"}.get(channel, channel)
    name = customer_name or chat_id[:16]

    # Telefonnummer ableiten (WhatsApp: chat_id = "wa_<nummer>") → Rückruf/Anschreiben
    phone = ""
    if channel == "whatsapp" and str(chat_id).startswith("wa_"):
        phone = str(chat_id)[3:]

    title = f"Support-Eskalation ({ch_label})"

    lines = [f"Kunde: {name}"]
    if reason:
        lines.append(f"Grund: {reason}")
    if phone:
        lines.append(f"📞 {phone}")
    if collected_info:
        lines.append(f"\n{collected_info}")

    message = "\n".join(lines)

    # Klickbarer Link in der Push: WhatsApp-Chat direkt öffnen (von dort aus auch
    # anrufbar). Telegram/Webchat haben keine direkt anwählbare Nummer → kein Link.
    url = f"https://wa.me/{phone}" if phone else None
    url_title = "💬 Kunde direkt anschreiben" if phone else None

    send_pushover(
        title=title,
        message=message,
        priority=1,  # High priority – plays sound even in quiet hours
        url=url,
        url_title=url_title,
    )


def notify_human_followup(chat_id, channel, text, customer_name=None):
    """Kunde schreibt WEITER, waehrend human_mode aktiv ist (wartet auf einen
    Menschen). Die Eskalations-Push kam evtl. vor Stunden — ohne Folge-Push
    wartet der Kunde ins Leere. Drosselung macht der Aufrufer (1/5 Min/Chat)."""
    ch_label = {"telegram": "Telegram", "whatsapp": "WhatsApp", "web": "Webchat"}.get(channel, channel)
    name = customer_name or chat_id[:16]
    phone = str(chat_id)[3:] if channel == "whatsapp" and str(chat_id).startswith("wa_") else ""

    lines = [f"Kunde: {name}"]
    if phone:
        lines.append(f"📞 {phone}")
    lines.append(f"\n„{text[:500]}“")

    send_pushover(
        title=f"Kunde wartet auf Antwort ({ch_label})",
        message="\n".join(lines),
        priority=0,
        url=f"https://wa.me/{phone}" if phone else None,
        url_title="💬 Kunde direkt anschreiben" if phone else None,
    )


def notify_api_outage(detail):
    """Alarm an Davide, wenn die Anthropic-API hart ausfaellt (z.B. Guthaben leer).

    Gedrosselt auf max. 1 Push / 30 Min, damit nicht jede fehlgeschlagene
    Kundennachricht eine eigene Benachrichtigung erzeugt.
    """
    try:
        if os.path.exists(_OUTAGE_THROTTLE_FILE):
            if (time.time() - os.path.getmtime(_OUTAGE_THROTTLE_FILE)) < _OUTAGE_THROTTLE_S:
                return  # erst kuerzlich alarmiert
        with open(_OUTAGE_THROTTLE_FILE, "w") as fh:
            fh.write(str(time.time()))
    except Exception as e:
        log.error(f"[pushover] outage-throttle: {e}")
        # Im Zweifel lieber senden als verschlucken → kein return

    send_pushover(
        title="🔴 DP Bot: AI-Ausfall",
        message=(detail + "\n\nDer Bot kann gerade keine Kundenanfragen beantworten. "
                 "Bitte Guthaben pruefen/aufladen."),
        priority=1,
        url="https://console.anthropic.com/settings/billing",
        url_title="Anthropic Guthaben aufladen",
    )


_VOICE_OUTAGE_THROTTLE_FILE = os.path.join(_BASE_DIR, ".voice_outage_alert.ts")


def notify_voice_outage(detail):
    """Alarm an Davide, wenn die Whisper-Transkription hart ausfaellt (OpenAI-Key/
    Guthaben). Eigene Drosselung (max. 1 Push / 30 Min). Der Bot laeuft sonst
    normal weiter — nur Sprachnachrichten werden nicht transkribiert."""
    try:
        if os.path.exists(_VOICE_OUTAGE_THROTTLE_FILE):
            if (time.time() - os.path.getmtime(_VOICE_OUTAGE_THROTTLE_FILE)) < _OUTAGE_THROTTLE_S:
                return
        with open(_VOICE_OUTAGE_THROTTLE_FILE, "w") as fh:
            fh.write(str(time.time()))
    except Exception as e:
        log.error(f"[pushover] voice-outage-throttle: {e}")

    send_pushover(
        title="🎤 DP Bot: Voice-Ausfall",
        message=detail + "\n\nText-Chat laeuft normal weiter.",
        priority=1,
        url="https://platform.openai.com/usage",
        url_title="OpenAI-Guthaben pruefen",
    )
