"""
Command handlers – /start, /reset, /warenkorb, /hilfe.
"""

from dp_connect_bot.config import BETA_HINT, log
from dp_connect_bot.models.response import BotResponse, Keyboard, KeyboardType, Button
from dp_connect_bot.services.cart_processing import format_cart


def handle_start(session):
    """Handle /start command – reset session, show mode choice."""
    name = session.get("customer_name", "")

    # Reset session but keep identity and last_order
    session["conversation"] = []
    session["cart"] = []
    session["status"] = "browsing"
    session["pending_selection"] = None
    session["mode"] = None
    session["hints_shown"] = {}
    session["support_step"] = None
    session["human_mode"] = False
    session["login_step"] = None
    session["login_email"] = None

    return BotResponse(
        text=(
            f"Hey{' ' + name if name else ''}! 👋\n\n"
            f"Willkommen bei *DP Connect*! Wie kann ich dir helfen?{BETA_HINT}"
        ),
        keyboards=[Keyboard(type=KeyboardType.MODE_CHOICE)],
    )


def handle_cart_display(session):
    """Handle /warenkorb command."""
    cart_text = format_cart(session)
    if session.get("cart"):
        cart_text += "\n\nSchreib *fertig* zum Bestellen! 🚀"
    return BotResponse(text=cart_text)


def handle_reset(session):
    """Handle /reset command."""
    session["cart"] = []
    session["conversation"] = []
    return BotResponse(text="Warenkorb und Gespräch zurückgesetzt. Was brauchst du?")


def handle_help():
    """Handle /hilfe command — zeigt die Kunden-Anleitung."""
    return handle_anleitung()


def handle_anleitung(channel="whatsapp"):
    """Kunden-Anleitung (Stichwort 'Anleitung', /anleitung, /hilfe).

    Inhalt liegt in prompts/anleitung.md — WICHTIG: bei jedem neuen
    Bot-Feature dort ergaenzen!
    """
    import os
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "prompts", "anleitung.md",
    )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read().strip()
    except Exception as e:
        log.error(f"Anleitung laden fehlgeschlagen: {e}")
        text = "Schreib mir einfach, was du brauchst — z.B. \"20 Elf Bar 800 Cherry\". 🛒"
    if channel == "web":
        # Webchat kann keine Sprachnachrichten/Fotos empfangen
        import re
        text = "\n".join(
            l for l in text.split("\n") if "🎤" not in l and "📸" not in l
        )
        text = re.sub(r"\n{3,}", "\n\n", text)
    return BotResponse(text=text)
