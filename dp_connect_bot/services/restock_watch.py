"""Wieder-da-Alarm — Kunden fuer ausverkaufte Produkte vormerken und bei Restock
benachrichtigen.

- Vormerken: add_watch(product_id, channel, recipient, name) (vom AI-Tool aufgerufen).
- Erkennen: check_and_notify() haengt im Cache-Reload-Hook (alle ~15 Min). Fuer jedes
  vorgemerkte, jetzt wieder lieferbare Produkt werden die Vormerker benachrichtigt.
- Versenden: Telegram sofort (proaktiv erlaubt). WhatsApp nur per genehmigter Meta-
  Vorlage (Template-Name in der Bot-Config `restock_wa_template`); ohne Template wird
  die Vormerkung GEHALTEN bis das Template gesetzt ist. Webchat: kein Push moeglich.
"""

import sqlite3
import time
from threading import Lock

from dp_connect_bot.config import HISTORY_DB_PATH, log

_lock = Lock()
_initialized = False


def _ensure(c):
    global _initialized
    if not _initialized:
        c.execute(
            "CREATE TABLE IF NOT EXISTS restock_watch ("
            "product_id TEXT, channel TEXT, recipient TEXT, name TEXT, created_at REAL, "
            "PRIMARY KEY (product_id, channel, recipient))"
        )
        _initialized = True


def add_watch(product_id, channel, recipient, name="") -> bool:
    """Merkt einen Kunden fuer ein Produkt vor (idempotent pro Kunde+Produkt)."""
    if not (product_id and channel and recipient):
        return False
    try:
        with _lock, sqlite3.connect(HISTORY_DB_PATH, timeout=5) as c:
            _ensure(c)
            c.execute(
                "INSERT OR REPLACE INTO restock_watch VALUES (?, ?, ?, ?, ?)",
                (str(product_id), channel, str(recipient), name or "", time.time()),
            )
        return True
    except Exception as e:
        log.error(f"restock add_watch: {e}")
        return False


def _watched_product_ids():
    with sqlite3.connect(HISTORY_DB_PATH, timeout=5) as c:
        _ensure(c)
        return [r[0] for r in c.execute("SELECT DISTINCT product_id FROM restock_watch").fetchall()]


def _watchers(product_id):
    with sqlite3.connect(HISTORY_DB_PATH, timeout=5) as c:
        _ensure(c)
        return c.execute(
            "SELECT channel, recipient, name FROM restock_watch WHERE product_id = ?",
            (str(product_id),),
        ).fetchall()


def _delete(product_id, channel, recipient):
    with sqlite3.connect(HISTORY_DB_PATH, timeout=5) as c:
        c.execute(
            "DELETE FROM restock_watch WHERE product_id = ? AND channel = ? AND recipient = ?",
            (str(product_id), channel, str(recipient)),
        )


def _dispatch(channel, recipient, product_name) -> bool:
    """Versendet die Restock-Benachrichtigung. Returns True, wenn die Vormerkung
    DANACH entfernt werden soll (gesendet oder einmaliger Versuch), False wenn sie
    GEHALTEN werden soll (z.B. WhatsApp ohne konfiguriertes Template)."""
    if channel == "telegram":
        try:
            from dp_connect_bot.adapters.telegram import TelegramAdapter
            txt = (f"🔔 Gute Nachricht! *{product_name}* ist wieder vorrätig bei DP Connect. 🎉\n\n"
                   "Sag mir einfach Bescheid, wenn ich's dir einpacken soll!")
            TelegramAdapter()._send_message(recipient, txt)
        except Exception as e:
            log.error(f"restock telegram send: {e}")
        return True  # one-shot (auch bei Fehler nicht ewig retrien)

    if channel == "whatsapp":
        from dp_connect_bot.services.bot_config import load_bot_config
        cfg = load_bot_config()
        tmpl = (cfg.get("restock_wa_template") or "").strip()
        if not tmpl:
            log.info(f"restock WhatsApp gehalten (kein Template gesetzt): {product_name} → {recipient}")
            return False  # halten, bis Template konfiguriert ist
        lang = (cfg.get("restock_wa_lang") or "de").strip()
        try:
            from dp_connect_bot.adapters.whatsapp import WhatsAppAdapter
            ok = WhatsAppAdapter().send_template(recipient, tmpl, [product_name], lang=lang)
        except Exception as e:
            log.error(f"restock whatsapp template send: {e}")
            ok = False
        if not ok:
            # Fehlgeschlagen (z.B. Template-Name/Sprache/Parameter passen nicht) →
            # Vormerkung HALTEN statt still loeschen, sonst verliert ein Konfig-Fehler
            # die Vormerker unwiederbringlich. Naechster Cache-Zyklus versucht es erneut.
            log.warning(f"restock WhatsApp Template-Send fehlgeschlagen → Vormerkung GEHALTEN "
                        f"(Template '{tmpl}', lang '{lang}', {product_name} → {recipient})")
        return bool(ok)

    # andere Kanaele (web) koennen nicht proaktiv benachrichtigt werden
    return True


def check_and_notify():
    """Nach jedem Cache-Reload aufgerufen: benachrichtigt Vormerker, deren Produkt
    wieder lieferbar ist. Fehler duerfen den Cache-Reload NIEMALS stoeren."""
    try:
        from dp_connect_bot.services.product_cache import cache
        pids = _watched_product_ids()
        if not pids:
            return
        notified = 0
        for pid in pids:
            try:
                if not cache.is_available(pid):
                    continue
                prod = cache.get_product_by_id(pid)
                name = (prod.get("title") if prod else "") or "Dein vorgemerktes Produkt"
                for channel, recipient, _wname in _watchers(pid):
                    if _dispatch(channel, recipient, name):
                        with _lock:
                            _delete(pid, channel, recipient)
                        notified += 1
            except Exception as e:
                log.error(f"restock check pid={pid}: {e}")
        if notified:
            log.info(f"restock: {notified} Wieder-da-Benachrichtigung(en) versendet")
    except Exception as e:
        log.error(f"restock check_and_notify: {e}")
