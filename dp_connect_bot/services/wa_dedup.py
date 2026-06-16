"""Dedup fuer WhatsApp-Webhooks.

Meta wiederholt die Webhook-Zustellung, wenn der Server nicht schnell genug mit
HTTP 200 antwortet. Der Bot verarbeitet aber synchron (KI-Call bis ~18s), daher
kann Meta dieselbe Nachricht erneut schicken. Ohne Entprellung wuerde sie doppelt
verarbeitet → doppelte Warenkorb-Adds, im schlimmsten Fall Doppelbestellung.

Entprellt wird ueber die stabile `msg.id` (gleich ueber alle Retries). SQLite-
gestuetzt, damit es auch ueber mehrere Worker-Prozesse hinweg greift.
"""

import sqlite3
import time
from threading import Lock

from dp_connect_bot.config import HISTORY_DB_PATH, log

_lock = Lock()
_TTL_S = 900  # 15 Min — Meta-Retries passieren binnen Minuten
_initialized = False


def _ensure(c):
    global _initialized
    if not _initialized:
        c.execute("CREATE TABLE IF NOT EXISTS wa_processed (msg_id TEXT PRIMARY KEY, ts REAL)")
        _initialized = True


def seen_before(msg_id) -> bool:
    """True, wenn diese WhatsApp-Nachricht schon verarbeitet wurde (Meta-Retry).

    Markiert sie atomar (INSERT OR IGNORE) als verarbeitet. Bei DB-Fehler: False
    (im Zweifel verarbeiten — lieber einmal zu viel als den Kunden ignorieren).
    """
    if not msg_id:
        return False
    try:
        with _lock, sqlite3.connect(HISTORY_DB_PATH, timeout=5) as c:
            _ensure(c)
            cur = c.execute(
                "INSERT OR IGNORE INTO wa_processed(msg_id, ts) VALUES (?, ?)",
                (str(msg_id), time.time()),
            )
            is_new = cur.rowcount == 1
            if is_new:
                # gelegentlich alte Eintraege aufraeumen
                c.execute("DELETE FROM wa_processed WHERE ts < ?", (time.time() - _TTL_S,))
            return not is_new
    except Exception as e:
        log.error(f"wa_dedup: {e}")
        return False
