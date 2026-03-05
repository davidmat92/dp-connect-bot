"""
Cart processing – parses cart_actions and keyboard triggers from Claude responses.
"""

import json
import re

from dp_connect_bot.config import WOOCOMMERCE_URL, log
from dp_connect_bot.models.response import Button, Keyboard, KeyboardType, WcAction
from dp_connect_bot.services.product_cache import cache
from dp_connect_bot.services.history import track_event
from dp_connect_bot.utils.formatting import (
    format_price_de, get_variant_display_name, parse_price, stock_label,
)


def process_cart_actions(session, ai_response):
    """Verarbeitet cart_actions UND Keyboard-Trigger aus Claude's Antwort.

    Returns: (clean_text, keyboards: list[Keyboard], wc_actions: list[WcAction])
    """
    pattern = r"```cart_action\n(.*?)\n```"
    matches = re.findall(pattern, ai_response, re.DOTALL)
    clean = re.sub(r"\s*```cart_action\n.*?\n```\s*", "", ai_response, flags=re.DOTALL).strip()

    keyboards = []
    wc_actions = []

    # Keyboard-Trigger extrahieren
    flavor_matches = re.findall(r"\[SHOW_FLAVORS:(\d+)\]", clean)
    quantity_matches = re.findall(r"\[SHOW_QUANTITIES:(\d+)\]", clean)
    has_callback_request = "[REQUEST_CALLBACK]" in clean

    # Tags aus dem Text entfernen
    clean = re.sub(r"\s*\[SHOW_FLAVORS:\d+\]\s*", "", clean).strip()
    clean = re.sub(r"\s*\[SHOW_QUANTITIES:\d+\]\s*", "", clean).strip()
    clean = re.sub(r"\s*\[REQUEST_CALLBACK\]\s*", "", clean).strip()

    for parent_id in flavor_matches:
        keyboards.append(build_flavor_keyboard(parent_id))
    for product_id in quantity_matches:
        keyboards.append(build_quantity_keyboard(product_id))

    # Cart Actions verarbeiten
    for match in matches:
        try:
            data = json.loads(match)
            action = data.get("action")

            if action == "add":
                pid = str(data["product_id"])
                qty = data.get("quantity", 1)
                product_check = cache.get_product_by_id(pid)
                if cache.available and not cache.is_available(pid):
                    if product_check:
                        log.warning(f"Cart add: Produkt {pid} ({product_check.get('title','')}) existiert aber nicht verfuegbar")
                        clean += f"\n\n⚠️ {product_check.get('title', 'Dieses Produkt')} ist leider aktuell nicht lieferbar."
                    else:
                        log.warning(f"Cart add: Produkt {pid} nicht im Cache gefunden")
                        clean += f"\n\n⚠️ Produkt-ID {pid} wurde nicht gefunden. Bitte nochmal versuchen."
                    continue
                existing = next((i for i in session["cart"] if str(i["product_id"]) == pid), None)
                if existing:
                    existing["quantity"] += qty
                else:
                    img_url = ""
                    if product_check:
                        img_url = product_check.get("image_url", "")
                        if not img_url and product_check.get("post_parent"):
                            parent_p = cache.get_product_by_id(product_check["post_parent"])
                            if parent_p:
                                img_url = parent_p.get("image_url", "")
                    session["cart"].append({
                        "product_id": pid,
                        "title": data.get("title", ""),
                        "quantity": qty,
                        "price": str(data.get("price", "")),
                        "image_url": img_url,
                    })
                n = len(session["cart"])
                clean += f"\n\n✅ Im Warenkorb! ({n} Produkt{'e' if n > 1 else ''})"
                wc_actions.append(WcAction(action="add", product_id=pid, quantity=qty))

            elif action == "remove":
                pid = str(data["product_id"])
                session["cart"] = [i for i in session["cart"] if str(i["product_id"]) != pid]
                wc_actions.append(WcAction(action="remove", product_id=pid))

            elif action == "clear":
                session["cart"] = []
                wc_actions.append(WcAction(action="clear"))

            elif action == "show_cart":
                clean += "\n\n" + format_cart(session)

            elif action == "checkout":
                if session["cart"]:
                    session["status"] = "checkout"
                    session["last_order"] = [dict(i) for i in session["cart"]]
                    url = generate_checkout_url(session["cart"])
                    clean += "\n\n" + format_cart(session)
                    if url:
                        clean += "\n\nDirekt zum Checkout:\n" + url
                else:
                    clean += "\n\nDein Warenkorb ist noch leer."

        except (json.JSONDecodeError, KeyError) as e:
            log.error(f"Cart action error: {e}")

    # Callback-Request
    if has_callback_request:
        keyboards.append(Keyboard(type=KeyboardType.CALLBACK))
        track_event("callback_offered", session.get("chat_id", ""), session.get("channel", ""))

    return clean, keyboards, wc_actions


def build_flavor_keyboard(parent_id):
    """Baut ein Flavor-Keyboard fuer ein Parent-Produkt."""
    variations = cache.get_variations_available(str(parent_id))
    buttons = []
    for v in variations:
        name = get_variant_display_name(v)
        price = format_price_de(v.get("price"))
        sl = stock_label(v.get("stock"))
        buttons.append(Button(
            text=name,
            callback_data=f"sel_{v['id']}",
            sublabel=price,
        ))
    return Keyboard(
        type=KeyboardType.FLAVORS,
        buttons=buttons,
        parent_id=str(parent_id),
    )


def build_quantity_keyboard(product_id):
    """Baut ein Mengen-Keyboard fuer ein Produkt."""
    product = cache.get_product_by_id(str(product_id))
    if not product:
        return Keyboard(type=KeyboardType.QUANTITIES, product_id=str(product_id))

    vpe = int(product.get("vpe") or 1)
    if vpe < 1:
        vpe = 1
    name = get_variant_display_name(product)
    price = format_price_de(product.get("price"))

    quantities = [vpe * m for m in [1, 2, 3, 5, 10]]
    buttons = [
        Button(text=str(q), callback_data=f"qty_{product_id}_{q}")
        for q in quantities
    ]

    return Keyboard(
        type=KeyboardType.QUANTITIES,
        buttons=buttons,
        product_id=str(product_id),
        label=name,
        price=price,
        vpe=str(vpe),
    )


def format_cart(session):
    """Formatiert den Warenkorb als Text."""
    if not session["cart"]:
        return "Warenkorb ist leer."
    lines = ["🛒 Dein Warenkorb:\n"]
    total = 0.0
    for item in session["cart"]:
        price = parse_price(item.get("price"))
        subtotal = price * item["quantity"]
        total += subtotal
        line = f"• {item['title']} x{item['quantity']}"
        if price:
            line += f" - {format_price_de(subtotal)}"
        lines.append(line)
    lines.append(f"\nGesamt (netto): {format_price_de(total)}")
    return "\n".join(lines)


def format_cart_rich(session):
    """Reichhaltige Warenkorb-Daten mit Bildern fuer Web-Channel."""
    if not session["cart"]:
        return {"items": [], "total": 0, "total_formatted": "0,00€"}
    items = []
    total = 0.0
    for item in session["cart"]:
        price = parse_price(item.get("price"))
        subtotal = price * item["quantity"]
        total += subtotal
        image_url = ""
        product = cache.get_product_by_id(item.get("product_id"))
        if product:
            image_url = product.get("image_url", "")
            if not image_url and product.get("post_parent"):
                parent = cache.get_product_by_id(product["post_parent"])
                if parent:
                    image_url = parent.get("image_url", "")
        items.append({
            "product_id": item["product_id"],
            "title": item["title"],
            "quantity": item["quantity"],
            "price": price,
            "price_formatted": format_price_de(price),
            "subtotal": subtotal,
            "subtotal_formatted": format_price_de(subtotal),
            "image_url": image_url,
        })
    return {
        "items": items,
        "total": total,
        "total_formatted": format_price_de(total),
    }


def generate_checkout_url(cart):
    """Generiert den WooCommerce Checkout URL."""
    if not cart:
        return None
    base = WOOCOMMERCE_URL.rstrip("/")
    if len(cart) == 1:
        item = cart[0]
        return base + "/warenkorb/?add-to-cart=" + str(item["product_id"]) + "&quantity=" + str(item["quantity"])
    items = "|".join(str(i["product_id"]) + ":" + str(i["quantity"]) for i in cart)
    return base + "/?dpbot_cart=" + items
