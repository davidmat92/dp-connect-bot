"""
Support flow handler – AI-First support with WooCommerce Tool Use.

Claude tries to solve common support issues autonomously using WooCommerce API.
Only escalates to human when necessary.

Also contains the Login-Hilfe button flow (structured, non-AI).
"""

from dp_connect_bot.config import log
from dp_connect_bot.models.response import BotResponse, Button, Keyboard, KeyboardType
from dp_connect_bot.services.claude_ai import call_claude_support
from dp_connect_bot.services.history import track_event


def handle_support_message(chat_id, text, session, channel):
    """AI-First Support: Claude versucht selbst zu loesen.

    Args:
        chat_id: Chat ID
        text: Kundennachricht
        session: Session dict
        channel: Channel name (telegram, whatsapp, web)

    Returns:
        BotResponse
    """
    log.info(f"Support message from {chat_id}: {text[:80]}...")

    # Call Claude with Tool Use
    response_text, escalated, escalation_info = call_claude_support(session, text)

    # Handle escalation
    if escalated and escalation_info:
        log.info(f"Support escalated for {chat_id}: {escalation_info.get('reason', '?')}")

        session["human_mode"] = True

        # Log escalation info in conversation for dashboard visibility
        ticket_info = (
            f"[SUPPORT-ESKALATION]\n"
            f"Grund: {escalation_info.get('reason', 'Nicht angegeben')}\n"
            f"Infos: {escalation_info.get('collected_info', 'Keine')}"
        )
        session["conversation"].append({"role": "assistant", "content": ticket_info})

        track_event("support_escalated", chat_id, channel, escalation_info.get("reason", ""))

        # Add mode choice keyboard so customer can switch back to ordering
        return BotResponse(
            text=response_text,
            keyboards=[Keyboard(type=KeyboardType.MODE_CHOICE)],
        )

    return BotResponse(text=response_text)


def handle_support_step(session, text, channel):
    """Legacy support step handler – kept for backward compatibility.

    In the new AI-first flow, this is no longer used.
    Returns None to pass through to the new flow.
    """
    # The old 3-step flow is replaced by handle_support_message.
    # This function exists only so imports don't break.
    return None


# ============================================================
# LOGIN-HILFE FLOW (button-driven, no AI)
# ============================================================

def handle_login_email(session, text):
    """Handle email input in login flow.

    Checks if account exists via WooCommerce API and shows
    appropriate button options.

    Args:
        session: Session dict
        text: User input (should be an email address)

    Returns:
        BotResponse with LOGIN_OPTIONS keyboard
    """
    from dp_connect_bot.services.woocommerce import wc_client

    email = text.strip().lower()

    # Basic email validation
    if "@" not in email or "." not in email:
        return BotResponse(
            text=(
                "Das sieht nicht nach einer E-Mail-Adresse aus. 🤔\n\n"
                "Bitte gib deine E-Mail-Adresse ein, z.B. *max@beispiel.de*"
            )
        )

    # Store email for later use
    session["login_email"] = email
    session["login_step"] = "choose_option"

    # Check account via WooCommerce
    try:
        result = wc_client.check_customer(email)
    except Exception as e:
        log.error(f"Login flow check_customer error: {e}")
        result = None

    if result is None:
        # API error
        return BotResponse(
            text=(
                "Da konnte ich gerade nicht drauf zugreifen. 😕\n\n"
                "Versuch's alternativ hier:\n"
                "👉 https://dpconnect.de/anmelden/?action=magic_login\n\n"
                "Oder ruf an: +49 221 650 878 78"
            ),
            keyboards=[Keyboard(type=KeyboardType.MODE_CHOICE)],
        )

    if result.get("exists"):
        # Account found → show login options
        name = result.get("name", "")
        greeting = f" ({name})" if name else ""

        return BotResponse(
            text=(
                f"✅ *Account gefunden!*{greeting}\n\n"
                f"Wie moechtest du dich einloggen?"
            ),
            keyboards=[Keyboard(
                type=KeyboardType.LOGIN_OPTIONS,
                buttons=[
                    Button(text="🔑 Login-Link (empfohlen)", callback_data="login_magic"),
                    Button(text="📧 Neues Passwort senden", callback_data="login_newpw"),
                ],
            )],
        )
    else:
        # No account found
        return BotResponse(
            text=(
                f"❌ Mit *{email}* gibt's leider keinen Account.\n\n"
                "Vielleicht eine andere E-Mail? Oder jetzt registrieren?"
            ),
            keyboards=[Keyboard(
                type=KeyboardType.LOGIN_OPTIONS,
                buttons=[
                    Button(text="📝 Jetzt registrieren", callback_data="login_register"),
                    Button(text="🔄 Andere E-Mail", callback_data="login_retry"),
                ],
            )],
        )
