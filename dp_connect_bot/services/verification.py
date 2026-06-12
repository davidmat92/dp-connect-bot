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

import re

import requests

from dp_connect_bot.config import TOOLS_API_BASE, TOOLS_VERIFY_TOKEN, log

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


def is_verified(session) -> bool:
    """Verifiziert = registrierter B2B-Kunde. Feature aus → immer True."""
    if not enabled():
        return True
    return bool(session.get("verified"))


def mark_verified(session, customer: dict):
    session["verified"] = {
        "customer_id": customer.get("id"),
        "name": customer.get("name", ""),
        "firma": customer.get("firma", ""),
        "email": customer.get("email", ""),
    }
    session.pop("verify_pending_email", None)
    if customer.get("name") and not session.get("customer_name"):
        session["customer_name"] = customer["name"]


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
