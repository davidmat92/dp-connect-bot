"""
Mode detection & gate – determines order vs support mode.
"""

from dp_connect_bot.config import BETA_HINT, log
from dp_connect_bot.models.response import BotResponse, Keyboard, KeyboardType
from dp_connect_bot.utils.hints import get_hint


# Produktsignale fuer Smart Detection
PRODUCT_KEYWORDS = {
    "elf bar", "elfbar", "elfa", "flerbar", "lost mary", "pods", "pod",
    "liquid", "vape", "einweg", "tabak", "shisha", "snacks", "drinks",
    "crystal", "köpfe", "dampfe", "energy", "kohle", "nikotinfrei",
    "bestellen", "bestell", "brauche", "brauch", "hätte gerne",
    "was habt ihr", "was gibt es", "zeig mir", "sortiment",
    "elf", "fler", "lost", "ske", "rand m", "al fakher",
    "nochmal", "nachbestellen", "das gleiche", "wie letztes mal",
}

# Support-Signale fuer Smart Detection
SUPPORT_KEYWORDS = {
    "wo bleibt", "wo ist meine bestellung", "bestellung verfolgen",
    "tracking", "sendungsverfolgung", "lieferstatus",
    "einloggen", "login", "anmelden", "kann mich nicht einloggen",
    "passwort", "kennwort", "registrierung", "registrieren",
    "keine zugangsdaten", "zugangsdaten",
    "rechnung", "invoice", "rechnungsadresse",
    "lieferadresse", "adresse ändern", "adresse aendern",
    "reklamation", "retoure", "rücksendung", "ruecksendung",
    "defekt", "kaputt", "beschaedigt", "beschädigt",
    "kundenservice", "support", "hilfe", "problem",
    "kundennummer", "mein konto", "mein account",
    "mit davide sprechen", "mit jemandem sprechen",
    "ich will einen menschen", "mitarbeiter",
}


def detect_mode(session, text, channel):
    """Smart mode detection. Returns BotResponse if mode gate should block, else None.

    Side effect: sets session["mode"] if detected.
    """
    if session.get("mode") is not None:
        return None

    if text.strip().startswith("/"):
        return None

    lower = text.strip().lower()

    # Detect support signals (check first – support takes priority over order)
    support_signals = any(kw in lower for kw in SUPPORT_KEYWORDS)
    if support_signals:
        session["mode"] = "support"
        session["support_step"] = None
        return None  # Let the message pass through to support handling

    # Detect product/order signals
    order_signals = any(kw in lower for kw in PRODUCT_KEYWORDS)

    # Numbers + words = likely an order (e.g. "50 cherry")
    if not order_signals and any(c.isdigit() for c in lower) and len(lower.split()) >= 2:
        order_signals = True

    if order_signals:
        session["mode"] = "order"
        return None  # Let the message pass through to normal handling

    if session.get("message_count", 0) <= 1:
        # First message, no product signal → show mode choice (all channels get buttons)
        name = session.get("customer_name", "")
        session["mode"] = "choosing"
        return BotResponse(
            text=f"Hey{' ' + name if name else ''}! 👋\n\nWie kann ich dir helfen?{BETA_HINT}",
            keyboards=[Keyboard(type=KeyboardType.MODE_CHOICE)],
        )

    # Subsequent messages without mode → assume order
    session["mode"] = "order"
    return None


def handle_whatsapp_mode_choice(session, text):
    """Handle WhatsApp text-based mode selection (1, 2 or 3) as fallback.

    Returns BotResponse or None if not a mode choice.
    """
    if session.get("mode") != "choosing":
        return None

    stripped = text.strip()
    if stripped == "1":
        session["mode"] = "order"
        voice_hint = get_hint(session, "voice_available")
        return BotResponse(
            text=(
                "🛒 *Bestell-Assistent* aktiv!\n\n"
                "Sag mir einfach was du brauchst, z.B.:\n"
                "• \"Elf Bar 800\"\n"
                "• \"50 Cherry und 30 Peach\"\n\n"
                f"Was darf's sein? 🚀{voice_hint}"
            )
        )
    elif stripped == "2":
        session["mode"] = "support"
        session["support_step"] = None
        return BotResponse(
            text=(
                "🎧 *Kundenservice*\n\n"
                "Klar, wie kann ich dir helfen? Beschreib mir einfach dein Anliegen – "
                "ich kann z.B. Bestellungen nachschlagen, Account-Probleme loesen "
                "oder dich an Davides Team weiterleiten. ✍️"
            )
        )
    elif stripped == "3":
        session["mode"] = "login_help"
        session["login_step"] = "ask_email"
        return BotResponse(
            text=(
                "🔑 *Login-Hilfe*\n\n"
                "Klar! Was ist deine E-Mail-Adresse, mit der du registriert bist? ✉️"
            )
        )

    return None


def is_human_mode(session):
    """Check if the session is in human takeover mode."""
    return session.get("human_mode", False)
