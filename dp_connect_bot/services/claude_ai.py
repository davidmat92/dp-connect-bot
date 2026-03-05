"""
Claude AI service – loads system prompt, calls Anthropic API.
"""

import os
import requests

from dp_connect_bot.config import ANTHROPIC_API_KEY, log
from dp_connect_bot.utils.formatting import parse_price


# Load system prompt from file
_PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
_PROMPT_PATH = os.path.join(_PROMPT_DIR, "system_prompt.md")

try:
    with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read()
except FileNotFoundError:
    log.error(f"System prompt nicht gefunden: {_PROMPT_PATH}")
    SYSTEM_PROMPT = "Du bist ein Verkaufsassistent fuer DP Connect."


def call_claude(session, user_message, product_context="", wc_cart=None):
    """Ruft Claude API auf und gibt die Antwort zurueck.

    Args:
        session: Session dict (with conversation, cart)
        user_message: Nachricht des Kunden
        product_context: Produktdaten-String von build_product_context
        wc_cart: WooCommerce Cart vom Frontend (optional, fuer Webchat)

    Returns:
        AI response text string
    """
    if not ANTHROPIC_API_KEY:
        return "Bot ist noch nicht konfiguriert (API Key fehlt). Bitte Admin kontaktieren."

    messages = list(session["conversation"][-16:])

    # Warenkorb IMMER mitschicken
    if wc_cart and isinstance(wc_cart, list) and len(wc_cart) > 0:
        cart_lines = []
        cart_total = 0.0
        for item in wc_cart:
            p = float(item.get("price", 0))
            qty = int(item.get("quantity", 1))
            sub = p * qty
            cart_total += sub
            name = item.get("name", "?")
            pid = item.get("product_id") or item.get("variation_id") or "?"
            cart_lines.append(f"  - {name} x{qty} à {p:.2f}€ = {sub:.2f}€ [ID:{pid}]")
        cart_str = f"AKTUELLER WARENKORB ({len(wc_cart)} Positionen, Gesamt: {cart_total:.2f}€ netto):\n" + "\n".join(cart_lines)
    elif session["cart"]:
        cart_lines = []
        cart_total = 0.0
        for item in session["cart"]:
            p = parse_price(item.get("price"))
            sub = p * item["quantity"]
            cart_total += sub
            cart_lines.append(f"  - {item['title']} x{item['quantity']} à {p}€ = {sub:.2f}€ [ID:{item['product_id']}]")
        cart_str = "AKTUELLER WARENKORB (" + str(len(session["cart"])) + " Produkte, Gesamt: " + f"{cart_total:.2f}€ netto):\n" + "\n".join(cart_lines)
    else:
        cart_str = "WARENKORB: Leer"

    if product_context:
        content = f"[PRODUKTDATEN]\n{product_context}\n\n[{cart_str}]\n\n[KUNDE]\n{user_message}"
    else:
        content = f"[{cart_str}]\n\n[KUNDE]\n{user_message}"

    messages.append({"role": "user", "content": content})

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1024,
                "system": SYSTEM_PROMPT,
                "messages": messages,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        ai_text = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")

        session["conversation"].append({"role": "user", "content": user_message})
        session["conversation"].append({"role": "assistant", "content": ai_text})
        return ai_text

    except Exception as e:
        log.error(f"Claude API Fehler: {e}")
        return "Da ist gerade was schiefgelaufen. Versuch's nochmal!"
