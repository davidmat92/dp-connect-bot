"""
WhatsApp-Sende-Warteschlange — bei Meta-Stoerungen gehen Bot-Antworten
sonst verloren. Fehlgeschlagene Sends werden gepuffert und nachgeliefert,
sobald die API wieder antwortet (Flush bei jedem eingehenden Webhook +
manuell via /admin/wa-flush).
"""

import json
import os
import threading
import time

import requests

from dp_connect_bot.config import WHATSAPP_API, WHATSAPP_PHONE_ID, WHATSAPP_TOKEN, log

QUEUE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "wa_send_queue.json",
)

_lock = threading.Lock()
_MAX_AGE = 12 * 3600   # aelter als 12h nicht nachliefern (24h-Fenster!)
_MAX_QUEUE = 500

# PERMANENTE Sende-Fehler — Retry/Puffern zwecklos, SOFORT verwerfen:
# 131026 unzustellbar, 131030 Empfaenger nicht erlaubt, 100 ungueltige Params,
# 131047/470 Re-Engagement (>24h-Fenster → braucht Template, kein Freitext),
# 131051 nicht unterstuetzter Typ, 131049 nicht zugestellt (Marketing-Limit).
# Sonst wuerde EINE solche Nachricht (z.B. Admin-Reply nach >24h) bei jedem Flush
# `api_down` setzen und die GANZE Queue bis zu 12h blockieren.
PERMANENT_SEND_ERRORS = (131026, 131030, 100, 131047, 470, 131051, 131049)


def _load() -> list:
    try:
        if os.path.exists(QUEUE_PATH):
            with open(QUEUE_PATH, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception as e:
        log.error(f"WA-Queue laden fehlgeschlagen: {e}")
    return []


def _save(queue: list):
    try:
        tmp = QUEUE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(queue[-_MAX_QUEUE:], fh, ensure_ascii=False)
        os.replace(tmp, QUEUE_PATH)
    except Exception as e:
        log.error(f"WA-Queue speichern fehlgeschlagen: {e}")


def enqueue(payload: dict):
    """Fehlgeschlagenen Send fuer spaeteren Retry puffern."""
    with _lock:
        queue = _load()
        queue.append({"ts": time.time(), "payload": payload})
        _save(queue)
    log.warning(f"WA-Send gepuffert (Queue: {len(queue)})")


def flush() -> dict:
    """Versucht alle gepufferten Sends erneut. Stoppt beim ersten Fehler
    (API offenbar noch down). Gibt {sent, dropped, remaining} zurueck."""
    with _lock:
        queue = _load()
        if not queue:
            return {"sent": 0, "dropped": 0, "remaining": 0}

        now = time.time()
        fresh = [q for q in queue if now - q.get("ts", 0) <= _MAX_AGE]
        dropped = len(queue) - len(fresh)
        sent = 0
        remaining = []
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
        api_down = False
        for item in fresh:
            if api_down:
                remaining.append(item)
                continue
            try:
                resp = requests.post(
                    f"{WHATSAPP_API}/{WHATSAPP_PHONE_ID}/messages",
                    headers=headers, json=item["payload"], timeout=10,
                )
                if resp.ok:
                    sent += 1
                else:
                    err = {}
                    try:
                        err = resp.json().get("error", {})
                    except Exception:
                        pass
                    # Permanente Fehler verwerfen (sonst blockiert EINE solche
                    # Nachricht via api_down die ganze Queue), echte API-/Auth-Fehler
                    # behalten und spaeter erneut.
                    if err.get("code") in PERMANENT_SEND_ERRORS:
                        dropped += 1
                    else:
                        remaining.append(item)
                        api_down = True
            except Exception:
                remaining.append(item)
                api_down = True

        _save(remaining)
        if sent or dropped:
            log.info(f"WA-Queue Flush: {sent} nachgeliefert, {dropped} verworfen, {len(remaining)} offen")
        return {"sent": sent, "dropped": dropped, "remaining": len(remaining)}


def pending_count() -> int:
    return len(_load())
