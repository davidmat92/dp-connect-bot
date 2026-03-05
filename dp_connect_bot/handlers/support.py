"""
Support flow handler – 3-step support ticket creation.
"""

from dp_connect_bot.config import CONFIRM_YES, log
from dp_connect_bot.models.response import BotResponse, Keyboard, KeyboardType
from dp_connect_bot.services.history import track_event


def handle_support_step(session, text, channel):
    """Handle the multi-step support flow.

    Returns BotResponse or None if not in support flow.
    """
    support_step = session.get("support_step")
    if not support_step:
        return None

    if support_step == "awaiting_issue":
        return _handle_awaiting_issue(session, text, channel)
    elif support_step == "confirm_contact":
        return _handle_confirm_contact(session, text, channel)
    elif support_step == "awaiting_contact":
        return _handle_awaiting_contact(session, text, channel)

    return None


def _handle_awaiting_issue(session, text, channel):
    """Step 1: Customer describes their issue."""
    session["support_issue"] = text.strip()
    user_info = session.get("user_info", {})
    known_contact = None
    contact_source = None

    if channel == "telegram":
        tg_user = user_info.get("tg_username", "")
        if tg_user:
            known_contact = f"@{tg_user}"
            contact_source = "telegram"
    elif channel == "web":
        wp_email = user_info.get("wp_email", "")
        if wp_email:
            known_contact = wp_email
            contact_source = "email"
    elif channel == "whatsapp":
        chat_id = session.get("chat_id", "")
        raw = chat_id.split("_", 1)[1] if "_" in chat_id else chat_id
        known_contact = raw
        contact_source = "whatsapp"

    if known_contact:
        session["support_step"] = "confirm_contact"
        session["support_contact"] = known_contact
        session["support_contact_type"] = contact_source
        if contact_source == "telegram":
            msg = f"👍 Danke! Sollen wir dich über Telegram ({known_contact}) kontaktieren?\n\nOder schreib mir deine E-Mail oder Telefonnummer falls du lieber anders erreichbar bist."
        elif contact_source == "email":
            msg = f"👍 Danke! Sollen wir dich per E-Mail ({known_contact}) kontaktieren?\n\nOder schreib mir eine andere E-Mail oder Telefonnummer."
        elif contact_source == "whatsapp":
            phone_display = known_contact if known_contact.startswith("+") else f"+{known_contact}"
            msg = f"👍 Danke! Sollen wir dich auf dieser WhatsApp-Nummer ({phone_display}) zurückschreiben?\n\nOder schreib mir eine andere Nummer oder E-Mail."
        else:
            msg = "👍 Danke! Wie sollen wir dich am besten erreichen?"
        return BotResponse(text=msg)
    else:
        session["support_step"] = "awaiting_contact"
        return BotResponse(
            text="👍 Danke! Wie sollen wir dich am besten erreichen?\n\nSchreib mir deine *E-Mail-Adresse* oder *Telefonnummer*. 📞"
        )


def _handle_confirm_contact(session, text, channel):
    """Step 2: Confirm or change contact info."""
    lower = text.strip().lower()
    if lower in CONFIRM_YES:
        return _create_ticket(session, channel)
    else:
        session["support_contact"] = text.strip()
        session["support_contact_type"] = "email" if "@" in text else "phone"
        return _create_ticket(session, channel)


def _handle_awaiting_contact(session, text, channel):
    """Step 3: Customer provides contact info."""
    session["support_contact"] = text.strip()
    session["support_contact_type"] = "email" if "@" in text else "phone"
    return _create_ticket(session, channel)


def _create_ticket(session, channel):
    """Create the support ticket and return confirmation."""
    name = session.get("customer_name", "Kunde")
    issue = session.get("support_issue", "Kein Anliegen angegeben")
    contact = session.get("support_contact", "Nicht angegeben")
    contact_type = session.get("support_contact_type", "unknown")

    # Save ticket in conversation for dashboard
    ticket_info = f"[SUPPORT-TICKET]\nKunde: {name}\nAnliegen: {issue}\nKontakt ({contact_type}): {contact}"
    session["conversation"].append({"role": "user", "content": f"[Anliegen: {issue}]"})
    session["conversation"].append({"role": "assistant", "content": ticket_info})

    session["human_mode"] = True
    session["support_step"] = None

    chat_id = session.get("chat_id", "")
    track_event("callback_requested", chat_id, channel, f"{contact_type}:{contact}")

    return BotResponse(
        text=(
            f"✅ *Perfekt, ist notiert!*\n\n"
            f"Dein Anliegen wurde an Davides Team weitergeleitet. "
            f"Wir melden uns so schnell wie möglich bei dir ({contact}).\n\n"
            f"Falls es dringend ist:\n"
            f"📞 +49 221 650 878 78\n"
            f"📧 info@dpconnect.de"
        ),
        keyboards=[Keyboard(type=KeyboardType.MODE_CHOICE)],
    )
