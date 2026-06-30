"""
Benachrichtigt tools.dpconnect.de, wenn ein Kunde MENSCHLICHE Hilfe braucht
(Support-Eskalation oder Rückruf-/Kontakt-Wunsch). Davide sieht das dann im
tools-Dashboard, nicht nur als Pushover-Push.

Fire-and-forget: die Benachrichtigung darf den Chat NIEMALS stören oder
verlangsamen — Fehler/Timeouts werden geschluckt.

tools-Seite (Davides Repo) muss POST {TOOLS_API_BASE}/api/bot-help-request
annehmen. Auth wie bei der Verifizierung: Header X-Bot-Verify-Token.
Payload-Contract siehe `notify_help_needed`.
"""

import threading

import requests

from dp_connect_bot.config import TOOLS_API_BASE, TOOLS_VERIFY_TOKEN, log

_HELP_PATH = "/api/bot-help-request"


def notify_help_needed(chat_id, channel, contact_pref="", issue="",
                       customer=None, customer_name=""):
    """Meldet tools.dpconnect.de, dass ein Kunde Hilfe braucht.

    Args:
        chat_id: Prefixed Chat-ID (z.B. "wa_4915...")
        channel: "whatsapp" | "telegram" | "web"
        contact_pref: Gewünschter Kontaktweg ("Rückruf" | "E-Mail" | "WhatsApp" | "")
        issue: Kurzes Anliegen (zusammengefasst aus dem Gespräch)
        customer: verified-Dict (customer_id/email/name/phone) falls verifiziert
        customer_name: Anzeigename des Kunden

    Payload-Contract (POST /api/bot-help-request, Header X-Bot-Verify-Token):
        {chat_id, channel, contact_pref, issue, customer_name,
         customer_id, phone, email}
    """
    if not TOOLS_API_BASE or not TOOLS_VERIFY_TOKEN:
        return  # tools-Anbindung nicht konfiguriert → still überspringen

    cust = customer or {}
    phone = ""
    if channel == "whatsapp" and chat_id:
        phone = str(chat_id).replace("wa_", "")
    phone = phone or cust.get("phone", "")

    payload = {
        "chat_id": chat_id,
        "channel": channel,
        "contact_pref": contact_pref,
        "issue": issue,
        "customer_name": customer_name or cust.get("name", ""),
        "customer_id": cust.get("customer_id") or cust.get("id") or "",
        "phone": phone,
        "email": cust.get("email", ""),
    }

    def _send():
        try:
            resp = requests.post(
                f"{TOOLS_API_BASE}{_HELP_PATH}",
                json=payload,
                headers={"X-Bot-Verify-Token": TOOLS_VERIFY_TOKEN,
                         "Content-Type": "application/json"},
                timeout=8,
            )
            if not resp.ok:
                log.warning(f"tools help-request {resp.status_code}: {resp.text[:160]}")
        except Exception as e:
            log.warning(f"tools help-request fehlgeschlagen: {e}")

    threading.Thread(target=_send, daemon=True).start()
