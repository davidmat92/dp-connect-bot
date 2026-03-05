"""
Support flow handler – AI-First support with WooCommerce Tool Use.

Claude tries to solve common support issues autonomously using WooCommerce API.
Only escalates to human when necessary.
"""

from dp_connect_bot.config import log
from dp_connect_bot.models.response import BotResponse, Keyboard, KeyboardType
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
