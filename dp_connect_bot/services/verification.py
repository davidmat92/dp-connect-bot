"""
B2B-Kundenverifizierung — Preise gibt es nur fuer registrierte Kunden.

Wege zur Verifizierung:
  - Webchat:  eingeloggter WP-User (kommt vom Widget mit)
  - WhatsApp: Telefonnummer-Match gegen die Kundendatenbank (via tools-API)
  - Fallback: E-Mail → 6-stelliger Code per Mail → Code im Chat

Das Daten-Matching laeuft IMMER ueber tools.dpconnect.de, nicht direkt
gegen WooCommerce. Ohne TOOLS_VERIFY_TOKEN ist das Feature aus
(alle Sessions gelten als verifiziert — Verhalten wie frueher).
"""

import json
import os
import re
import threading

import requests

from dp_connect_bot.config import TOOLS_API_BASE, TOOLS_VERIFY_TOKEN, log

# Persistenter Verified-Speicher — Sessions laufen nach 24h ab, die
# Verifizierung soll aber dauerhaft gelten (sonst muesste z.B. ein
# Telegram-Kunde jeden Tag neu den Code eingeben).
VERIFIED_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "verified_contacts.json",
)
_store_lock = threading.Lock()
_store_cache = None
_store_mtime = None

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
CODE_RE = re.compile(r"^\s*(\d{6})\s*$")


def enabled() -> bool:
    return bool(TOOLS_VERIFY_TOKEN)


def _post(path: str, payload: dict) -> dict:
    resp = requests.post(
        f"{TOOLS_API_BASE}{path}",
        json=payload,
        headers={"X-Bot-Verify-Token": TOOLS_VERIFY_TOKEN, "Content-Type": "application/json"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def lookup_phone(phone: str) -> dict:
    """{found: bool, customer: {...}} — Fehler werden als not-found behandelt,
    aber mit error-Flag (damit wir es spaeter erneut versuchen koennen)."""
    try:
        return _post("/api/bot-verify/phone-lookup", {"phone": phone})
    except Exception as e:
        log.error(f"verify phone_lookup fehlgeschlagen: {e}")
        return {"found": False, "error": True}


def send_code(email: str) -> dict:
    try:
        return _post("/api/bot-verify/send-code", {"email": email})
    except Exception as e:
        log.error(f"verify send_code fehlgeschlagen: {e}")
        return {"exists": False, "sent": False, "error": True}


def check_code(email: str, code: str) -> dict:
    try:
        return _post("/api/bot-verify/check-code", {"email": email, "code": code})
    except Exception as e:
        log.error(f"verify check_code fehlgeschlagen: {e}")
        return {"valid": False, "error": True}


def _load_store() -> dict:
    global _store_cache, _store_mtime
    with _store_lock:
        try:
            mtime = os.path.getmtime(VERIFIED_STORE_PATH) if os.path.exists(VERIFIED_STORE_PATH) else None
        except OSError:
            mtime = None
        if _store_cache is not None and mtime == _store_mtime:
            return _store_cache
        try:
            if mtime is not None:
                with open(VERIFIED_STORE_PATH, "r", encoding="utf-8") as fh:
                    _store_cache = json.load(fh)
            else:
                _store_cache = {}
            _store_mtime = mtime
        except Exception as e:
            log.error(f"Verified-Store laden fehlgeschlagen: {e}")
            _store_cache = _store_cache or {}
        return _store_cache


def get_stored_verification(chat_id: str):
    """Frueher verifizierte Kontakte bleiben verifiziert (ueberlebt Session-Ablauf)."""
    return _load_store().get(str(chat_id))


def _store_verification(chat_id: str, verified: dict):
    global _store_cache, _store_mtime
    with _store_lock:
        store = dict(_store_cache or {})
        store[str(chat_id)] = verified
        try:
            tmp = VERIFIED_STORE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(store, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, VERIFIED_STORE_PATH)
            _store_cache = store
            _store_mtime = os.path.getmtime(VERIFIED_STORE_PATH)
        except Exception as e:
            log.error(f"Verified-Store speichern fehlgeschlagen: {e}")


def is_verified(session) -> bool:
    """Verifiziert = registrierter B2B-Kunde. Feature aus → immer True."""
    if not enabled():
        return True
    return bool(session.get("verified"))


def mark_verified(session, customer: dict, chat_id: str = None):
    session["verified"] = {
        "customer_id": customer.get("id"),
        "name": customer.get("name", ""),
        "firma": customer.get("firma", ""),
        "email": customer.get("email", ""),
    }
    session.pop("verify_pending_email", None)
    if customer.get("name") and not session.get("customer_name"):
        session["customer_name"] = customer["name"]
    if chat_id:
        _store_verification(chat_id, session["verified"])


_PRICE_RE = re.compile(r"\d{1,5}[.,]\d{2}\s*€")
_SONDER_RE = re.compile(r"\|?\s*Sonderpreis[^\n|]*", re.IGNORECASE)


def strip_prices(text: str) -> str:
    """Entfernt Preise aus Produktdaten fuer unverifizierte Kontakte.

    Ein zentraler, injection-sicherer Filter — Preisschutz haengt NICHT
    am Wohlverhalten des Modells.
    """
    if not text:
        return text
    text = _SONDER_RE.sub("", text)
    return _PRICE_RE.sub("[Preis nach Login]", text)
