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


def detect_mode(session, text, channel):
    """Smart mode detection. Returns BotResponse if mode gate should block, else None.

    Side effect: sets session["mode"] if detected.
    """
    if session.get("mode") is not None:
        return None

    if text.strip().startswith("/"):
        return None

    lower = text.strip().lower()

    # Detect product/order signals
    order_signals = any(kw in lower for kw in PRODUCT_KEYWORDS)

    # Numbers + words = likely an order (e.g. "50 cherry")
    if not order_signals and any(c.isdigit() for c in lower) and len(lower.split()) >= 2:
        order_signals = True

    if order_signals:
        session["mode"] = "order"
        return None  # Let the message pass through to normal handling

    if session.get("message_count", 0) <= 1:
        # First message, no product signal → show mode choice
        name = session.get("customer_name", "")
        if channel == "whatsapp":
            session["mode"] = "choosing"
            return BotResponse(
                text=(
                    f"Hey{' ' + name if name else ''}! 👋\n\n"
                    f"Wie kann ich dir helfen?\n\n"
                    f"Schreib *1* für 🛒 *Bestellen*\n"
                    f"Schreib *2* für 🎧 *Kundenservice*{BETA_HINT}"
                )
            )
        else:
            return BotResponse(
                text=f"Hey{' ' + name if name else ''}! 👋\n\nWie kann ich dir helfen?",
                keyboards=[Keyboard(type=KeyboardType.MODE_CHOICE)],
            )

    # Subsequent messages without mode → assume order
    session["mode"] = "order"
    return None


def handle_whatsapp_mode_choice(session, text):
    """Handle WhatsApp text-based mode selection (1 or 2).

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
        session["support_step"] = "awaiting_issue"
        return BotResponse(
            text=(
                "🎧 *Kundenservice*\n\n"
                "Klar, ich leite dich weiter! Beschreib mir kurz dein Anliegen, "
                "damit Davides Team direkt Bescheid weiß. ✍️"
            )
        )

    return None


def is_human_mode(session):
    """Check if the session is in human takeover mode."""
    return session.get("human_mode", False)
