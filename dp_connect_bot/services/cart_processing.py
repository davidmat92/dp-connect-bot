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
    # Block durch Absatz ersetzen statt loeschen — sonst kleben die
    # umliegenden Saetze zusammen ("...Mengen!Gesamtsumme: ...")
    clean = re.sub(r"\s*```cart_action\n.*?\n```\s*", "\n\n", ai_response, flags=re.DOTALL).strip()
    clean = re.sub(r"\n{3,}", "\n\n", clean)

    # Harte Sperre: Unverifizierte koennen nicht bestellen (injection-sicher,
    # haengt nicht am Wohlverhalten des Modells)
    from dp_connect_bot.services.verification import is_verified
    if not is_verified(session):
        if matches:
            log.warning("Cart-Actions von unverifizierter Session verworfen")
            clean += "\n\n🔒 Bestellen geht erst nach kurzer Verifizierung als DP-Connect-Kunde."
        # Keyboards mit Preisen ebenfalls unterdruecken
        ai_response = re.sub(r"\[SHOW_(FLAVORS|QUANTITIES):\d+\]", "", ai_response)
        clean = re.sub(r"\s*\[SHOW_(FLAVORS|QUANTITIES):\d+\]\s*", " ", clean).strip()
        matches = []

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
    added_count = 0  # Anzahl erfolgreicher add-Actions → EINE Sammel-Bestaetigung
    add_parse_failed = False  # cart_action war fehlerhaft/unvollstaendig
    for match in matches:
        try:
            data = json.loads(match)
            action = data.get("action")

            if action == "add":
                pid = str(data["product_id"])
                qty = data.get("quantity", 1)
                product_check = cache.get_product_by_id(pid)

                # VPE enforcement: round up quantity to next VPE multiple
                if product_check:
                    try:
                        vpe = int(product_check.get("vpe") or 1)
                    except (ValueError, TypeError):
                        vpe = 1
                    if vpe > 1 and qty % vpe != 0:
                        old_qty = qty
                        qty = ((qty // vpe) + 1) * vpe
                        data["quantity"] = qty
                        clean += f"\n\n📦 Wird in {vpe}er-Packs geliefert – ich pack dir {qty} statt {old_qty} ein!"

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
                    # Preis: bevorzugt aus dem cart_action, sonst Live-Preis aus
                    # dem Cache — ein Produkt darf NIE mit leerem/0-Preis in den
                    # Warenkorb (Kunde wuerde sonst 0,00€ sehen).
                    price = data.get("price", "")
                    if (not price or str(price).strip() in ("", "0", "0.0")) and product_check:
                        price = product_check.get("price", "") or price
                    session["cart"].append({
                        "product_id": pid,
                        "title": data.get("title", ""),
                        "quantity": qty,
                        "price": str(price),
                        "image_url": img_url,
                    })
                added_count += 1
                wc_actions.append(WcAction(action="add", product_id=pid, quantity=qty))

            elif action == "remove":
                pid = str(data["product_id"])
                session["cart"] = [i for i in session["cart"] if str(i["product_id"]) != pid]
                wc_actions.append(WcAction(action="remove", product_id=pid))

            elif action == "set_qty":
                # Neue GESAMT-Menge fuer eine bestehende Position ("mach 30 draus")
                pid = str(data["product_id"])
                qty = int(data.get("quantity", 0))
                item = next((i for i in session["cart"] if str(i["product_id"]) == pid), None)
                if not item:
                    clean += f"\n\n⚠️ Das Produkt ist nicht im Warenkorb – nichts geändert."
                elif qty <= 0:
                    session["cart"] = [i for i in session["cart"] if str(i["product_id"]) != pid]
                    clean += f"\n\n🗑️ {item['title']} aus dem Warenkorb entfernt."
                    wc_actions.append(WcAction(action="remove", product_id=pid))
                else:
                    product_check = cache.get_product_by_id(pid)
                    if product_check:
                        try:
                            vpe = int(product_check.get("vpe") or 1)
                        except (ValueError, TypeError):
                            vpe = 1
                        if vpe > 1 and qty % vpe != 0:
                            old_qty = qty
                            qty = ((qty // vpe) + 1) * vpe
                            clean += f"\n\n📦 Wird in {vpe}er-Packs geliefert – ich mach {qty} statt {old_qty} draus!"
                    item["quantity"] = qty
                    clean += f"\n\n✏️ Menge angepasst: {item['title']} → {qty} Stück"
                    # Webchat-Sync kennt kein set_qty: remove + add ergibt dieselbe Menge
                    wc_actions.append(WcAction(action="remove", product_id=pid))
                    wc_actions.append(WcAction(action="add", product_id=pid, quantity=qty))

            elif action == "clear":
                session["cart"] = []
                wc_actions.append(WcAction(action="clear"))

            elif action == "show_cart":
                clean += "\n\n" + format_cart(session)

            elif action == "checkout":
                if session["cart"]:
                    session["status"] = "checkout"
                    session["last_order"] = [dict(i) for i in session["cart"]]
                    clean += "\n\n" + format_cart(session)

                    # Chat-Direktbestellung (Toggle: Bot-System → Einstellungen):
                    # verifizierte Kunden bestellen ohne Website
                    from dp_connect_bot.services.bot_config import load_bot_config
                    verified = session.get("verified") or {}
                    offered_chat_order = False
                    if load_bot_config().get("chat_checkout_enabled") and verified.get("customer_id"):
                        from dp_connect_bot.services.chat_order import get_customer_order_info
                        info = get_customer_order_info(verified["customer_id"])
                        if info.get("ok") and info.get("has_address"):
                            session["pending_chat_order"] = {
                                "rechnung_erlaubt": bool(info.get("rechnung_erlaubt")),
                            }
                            clean += "\n\n📦 *Lieferung an:*\n" + info.get("address_text", "")
                            clean += "\n\nWie möchtest du abschließen? 👇"
                            order_buttons = []
                            if info.get("rechnung_erlaubt"):
                                order_buttons.append(Button(text="🧾 Auf Rechnung", callback_data="chatorder_rechnung"))
                            order_buttons.append(Button(text="💳 Vorkasse", callback_data="chatorder_vorkasse"))
                            order_buttons.append(Button(text="🌐 Im Browser zahlen", callback_data="chatorder_link"))
                            keyboards.append(Keyboard(type=KeyboardType.CHAT_ORDER, buttons=order_buttons))
                            offered_chat_order = True

                    if not offered_chat_order:
                        # Magic-Checkout-Link fuer verifizierte Kunden, sonst Standard-Link
                        url = None
                        if verified.get("email"):
                            from dp_connect_bot.services.woocommerce import request_checkout_token
                            url = request_checkout_token(verified["email"], session["cart"])
                            if url:
                                clean += ("\n\n✨ Dein persönlicher Bestell-Link "
                                          "(loggt dich automatisch ein, Warenkorb ist schon gefüllt):\n" + url +
                                          "\n_Gültig für 15 Minuten._")
                        if not url:
                            url = generate_checkout_url(session["cart"])
                            if url:
                                clean += "\n\nDirekt zum Checkout:\n" + url
                else:
                    clean += "\n\nDein Warenkorb ist noch leer."

        except (json.JSONDecodeError, KeyError) as e:
            log.error(f"Cart action error: {e}")
            # Nur bei einer fehlgeschlagenen ADD-Aktion warnen (clear/checkout
            # ohne Erfolg soll keinen Einpack-Hinweis erzeugen).
            if '"add"' in match or "'add'" in match:
                add_parse_failed = True

    # EINE Sammel-Bestaetigung statt einer Zeile pro Produkt
    if added_count:
        n = len(session["cart"])
        clean += f"\n\n✅ Im Warenkorb! ({n} Produkt{'e' if n > 1 else ''})"
    elif add_parse_failed:
        # Eine add-Aktion war fehlerhaft und es wurde NICHTS eingepackt → der
        # Kunde darf nicht faelschlich denken, es sei alles im Warenkorb.
        clean += ("\n\n⚠️ Das Einpacken hat gerade nicht geklappt — sag mir bitte nochmal "
                  "kurz Produkt und Menge, dann pack ich's sicher für dich ein!")

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
    """Formatiert den Warenkorb chat-tauglich (WhatsApp/Telegram/Web).

    Keine Markdown-Tabellen — die rendern in Messengern nicht!
    """
    if not session["cart"]:
        return "🛒 Dein Warenkorb ist noch leer."
    lines = ["🛒 *Dein Warenkorb*", ""]
    total = 0.0
    for item in session["cart"]:
        price = parse_price(item.get("price"))
        subtotal = price * item["quantity"]
        total += subtotal
        emoji = _cart_item_emoji(item)
        lines.append(f"{emoji} *{item['title']}*")
        if price:
            lines.append(f"      {item['quantity']} Stk × {format_price_de(price)} = {format_price_de(subtotal)}")
        else:
            lines.append(f"      {item['quantity']} Stk")
    lines.append("")
    lines.append("➖➖➖➖➖➖➖➖")
    lines.append(f"💰 *Gesamt: {format_price_de(total)}* (netto)")
    if total >= 1000:
        lines.append("🚚 Kostenloser Versand ✅")
    elif total >= 850:
        lines.append(f"🚚 Noch {format_price_de(1000 - total)} bis zum kostenlosen Versand!")
    return "\n".join(lines)


def _cart_item_emoji(item):
    """Passendes Emoji je Produkt (Geschmack/Kategorie), Fallback 🛍️."""
    text = item.get("title", "").lower()
    product = cache.get_product_by_id(item.get("product_id"))
    if product:
        text += " " + product.get("category", "").lower() + " " + product.get("geschmack", "").lower()
    for words, emoji in (
        (("watermelon", "melone"), "🍉"), (("cherry", "kirsch"), "🍒"),
        (("peach", "pfirsich"), "🍑"), (("apple", "apfel"), "🍏"),
        (("blueberry", "blaubeere", "berry", "beere"), "🫐"),
        (("grape", "traube"), "🍇"), (("banana",), "🍌"), (("mango",), "🥭"),
        (("lemon", "zitrone", "lime"), "🍋"), (("strawberry", "erdbeer"), "🍓"),
        (("cola",), "🥤"), (("ice", "frozen", "cool"), "🧊"),
        (("tabak", "shisha"), "💨"), (("kohle",), "🔥"),
        (("liquid",), "💧"), (("pod",), "🔋"), (("vape", "puff"), "💨"),
        (("snack", "chips", "schoko", "candy"), "🍬"), (("drink", "energy"), "🥤"),
    ):
        if any(w in text for w in words):
            return emoji
    return "🛍️"


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
