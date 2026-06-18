"""Proaktive Nachbestell-Erinnerung.

Geht (per externem Cron → /admin/reorder-reminders) ueber die VERIFIZIERTEN Kontakte,
ermittelt mit dem reorder_engine, wer fuer eine Nachbestellung FAELLIG ist, und schickt
eine freundliche Erinnerung — Telegram live, WhatsApp nur per genehmigter Meta-Vorlage
(bot_config `reorder_wa_template`). Webchat kann nicht proaktiv gepusht werden.

SICHERHEIT:
- Standardmaessig AUS (`reorder_reminders_enabled` in bot_config). dry_run=True sendet
  NICHTS, gibt nur die Kandidaten zurueck.
- Drosselung pro Kontakt (REMIND_COOLDOWN_DAYS) — kein Spam, auch wenn der Kunde
  faellig bleibt.
- Voll defensiv: ein Fehler pro Kontakt stoppt den Lauf nie.
"""

import sqlite3
import time
from threading import Lock

from dp_connect_bot.config import HISTORY_DB_PATH, log

_lock = Lock()
_initialized = False

REMIND_COOLDOWN_DAYS = 5      # fruehestens nach 5 Tagen erneut erinnern
MAX_PER_RUN = 300             # Laufzeit der Request begrenzen


def _ensure(c):
    global _initialized
    if not _initialized:
        c.execute("CREATE TABLE IF NOT EXISTS reorder_reminded ("
                  "chat_id TEXT PRIMARY KEY, last_ts REAL)")
        _initialized = True


def _recently_reminded(chat_id) -> bool:
    try:
        with sqlite3.connect(HISTORY_DB_PATH, timeout=5) as c:
            _ensure(c)
            row = c.execute("SELECT last_ts FROM reorder_reminded WHERE chat_id=?",
                            (str(chat_id),)).fetchone()
            if not row:
                return False
            return (time.time() - row[0]) < REMIND_COOLDOWN_DAYS * 86400
    except Exception as e:
        log.error(f"reorder reminder throttle-read: {e}")
        return True  # im Zweifel NICHT erneut senden


def _mark_reminded(chat_id):
    try:
        with _lock, sqlite3.connect(HISTORY_DB_PATH, timeout=5) as c:
            _ensure(c)
            c.execute("INSERT OR REPLACE INTO reorder_reminded VALUES (?, ?)",
                      (str(chat_id), time.time()))
    except Exception as e:
        log.error(f"reorder reminder throttle-write: {e}")


def _tg_message(name, r):
    top = ", ".join(r.get("top_products", [])[:3])
    hello = f"Hey{' ' + name if name else ''}! 👋"
    body = (f"{hello}\n\nDu bestellst normalerweise etwa alle {r['avg_interval_days']} Tage — "
            f"deine letzte Bestellung ist schon {r['days_since_last']} Tage her. "
            "Zeit zum Auffüllen? 🛒")
    if top:
        body += f"\n\nDein Übliches: {top}"
    body += "\n\nSchreib einfach *\"nochmal das gleiche\"* — oder sag mir, was du brauchst!"
    return body


def _dispatch(chat_id, channel, name, r) -> bool:
    """True = gesendet (Drosselung setzen). False = halten/uebersprungen."""
    recipient = chat_id.split("_", 1)[1] if "_" in chat_id else chat_id
    if channel == "telegram":
        try:
            from dp_connect_bot.adapters.telegram import TelegramAdapter
            return bool(TelegramAdapter()._send_message(recipient, _tg_message(name, r)))
        except Exception as e:
            log.error(f"reorder reminder TG send: {e}")
            return False
    if channel == "whatsapp":
        from dp_connect_bot.services.bot_config import load_bot_config
        cfg = load_bot_config()
        tmpl = (cfg.get("reorder_wa_template") or "").strip()
        if not tmpl:
            log.info(f"reorder reminder WA gehalten (kein Template): {chat_id}")
            return False  # halten, bis Template gesetzt
        lang = (cfg.get("reorder_wa_lang") or "de").strip()
        try:
            from dp_connect_bot.adapters.whatsapp import WhatsAppAdapter
            # Template-Variablen: {{1}}=Name (oder "du"), {{2}}=Tage seit letzter
            ok = WhatsAppAdapter().send_template(
                recipient, tmpl, [name or "du", str(r["days_since_last"])], lang=lang)
            return bool(ok)
        except Exception as e:
            log.error(f"reorder reminder WA template: {e}")
            return False
    return False


def check_and_remind(dry_run=False) -> dict:
    """Sucht faellige verifizierte Kunden und erinnert sie. dry_run=True sendet nichts."""
    try:
        from dp_connect_bot.services.bot_config import load_bot_config
        enabled = bool(load_bot_config().get("reorder_reminders_enabled"))
        if not enabled and not dry_run:
            return {"ok": True, "enabled": False, "sent": 0,
                    "note": "reorder_reminders_enabled ist AUS"}

        from dp_connect_bot.services.verification import _load_store
        from dp_connect_bot.services.reorder_engine import analyze
        store = dict(_load_store() or {})

        candidates, sent, checked = [], 0, 0
        for chat_id, v in store.items():
            if checked >= MAX_PER_RUN:
                break
            try:
                cust = (v or {}).get("customer_id")
                if not cust:
                    continue
                if chat_id.startswith("tg_"):
                    channel = "telegram"
                elif chat_id.startswith("wa_"):
                    channel = "whatsapp"
                else:
                    continue  # web → kein Push
                if _recently_reminded(chat_id):
                    continue
                checked += 1
                r = analyze(cust)
                if not (r.get("ok") and r.get("enough_history") and r.get("due")):
                    continue
                candidates.append({
                    "chat_id": chat_id, "channel": channel,
                    "name": v.get("name", ""),
                    "days_since_last": r["days_since_last"],
                    "avg_interval_days": r["avg_interval_days"],
                    "_r": r,
                })
            except Exception as e:
                log.error(f"reorder reminder pro Kontakt {chat_id}: {e}")

        if not dry_run:
            for cand in candidates:
                if _dispatch(cand["chat_id"], cand["channel"], cand["name"], cand["_r"]):
                    _mark_reminded(cand["chat_id"])
                    sent += 1

        detail = [{k: c[k] for k in ("chat_id", "channel", "days_since_last", "avg_interval_days")}
                  for c in candidates]
        log.info(f"reorder reminders: {len(candidates)} faellig, {sent} gesendet "
                 f"(dry_run={dry_run}, geprueft={checked})")
        return {"ok": True, "enabled": enabled, "dry_run": dry_run,
                "checked": checked, "due": len(candidates), "sent": sent, "detail": detail}
    except Exception as e:
        log.error(f"reorder reminders check_and_remind: {e}")
        return {"ok": False, "error": str(e)[:200]}
