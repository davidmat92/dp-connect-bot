"""
Cart handlers – checkout, cart display, reorder, pending quantity.
"""

from dp_connect_bot.config import CHECKOUT_WORDS, CART_DISPLAY_WORDS, REORDER_TRIGGERS, CATEGORY_MAP, BROWSE_TRIGGERS, log
from dp_connect_bot.models.response import BotResponse, Keyboard, KeyboardType, Button
from dp_connect_bot.services.cart_processing import format_cart, generate_checkout_url
from dp_connect_bot.services.product_cache import cache
from dp_connect_bot.services.history import track_event
from dp_connect_bot.utils.formatting import format_price_de, parse_price, get_variant_display_name
from dp_connect_bot.utils.hints import get_hint


def handle_checkout(session, channel):
    """Handle checkout request when cart is not empty."""
    if not session.get("cart"):
        return None

    n = len(session["cart"])
    total = sum(parse_price(i.get("price", "0")) * i.get("quantity", 0) for i in session["cart"])
    url = generate_checkout_url(session["cart"])
    session["status"] = "checkout"
    session["last_order"] = list(session["cart"])

    cart_summary = "\n".join(
        f"  ✅ {i['quantity']}x {i['title']}" for i in session["cart"]
    )

    checkout_hint = get_hint(session, "checkout_done")

    return BotResponse(
        text=(
            f"🛒 *Deine Bestellung ({n} Produkt{'e' if n > 1 else ''}):*\n\n"
            f"{cart_summary}\n\n"
            f"💰 Gesamt: {format_price_de(total)} netto\n\n"
            f"👉 [Jetzt bestellen]({url})\n\n"
            f"Der Link bringt dich direkt zum Checkout! 🚀"
            f"{checkout_hint}"
        ),
        checkout_url=url or "",
    )


def handle_cart_view(session):
    """Handle cart view request."""
    if session.get("cart"):
        n = len(session["cart"])
        total = sum(parse_price(i.get("price", "0")) * i.get("quantity", 0) for i in session["cart"])
        cart_summary = "\n".join(
            f"  • {i['quantity']}x {i['title']} – {format_price_de(parse_price(i.get('price', '0')) * i.get('quantity', 0))}"
            for i in session["cart"]
        )
        return BotResponse(
            text=(
                f"🛒 *Dein Warenkorb ({n} Produkt{'e' if n > 1 else ''}):*\n\n"
                f"{cart_summary}\n\n"
                f"💰 Gesamt: {format_price_de(total)} netto\n\n"
                f"Schreib *fertig* zum Bestellen oder sag mir was noch dazu soll! 👍"
            )
        )
    else:
        return BotResponse(text="🛒 Dein Warenkorb ist noch leer. Sag mir was du brauchst! 😊")


def handle_reorder(session, channel):
    """Handle reorder request."""
    last_order = session.get("last_order", [])
    if not last_order:
        return None  # Let Claude handle it

    if channel in ("telegram", "whatsapp"):
        cart_summary = "\n".join(f"  • {i['quantity']}x {i['title']}" for i in last_order)
        return BotResponse(
            text=f"🔄 Deine letzte Bestellung:\n\n{cart_summary}\n\nSoll ich die nochmal einpacken?",
            keyboards=[Keyboard(type=KeyboardType.REORDER_CONFIRM)],
        )
    else:
        # Webchat: direkt laden
        session["cart"] = [dict(i) for i in last_order]
        n = len(session["cart"])
        total = sum(parse_price(i.get("price", "0")) * i.get("quantity", 0) for i in session["cart"])
        cart_summary = "\n".join(f"  ✅ {i['quantity']}x {i['title']}" for i in session["cart"])
        return BotResponse(
            text=(
                f"🔄 Letzte Bestellung geladen!\n\n{cart_summary}\n\n"
                f"💰 Gesamt: {format_price_de(total)} netto\n\n"
                f"Schreib *fertig* zum Bestellen oder sag mir was du ändern willst! ✏️"
            )
        )


def handle_browse(session, channel):
    """Handle browse/category overview request."""
    if channel in ("telegram", "web"):
        buttons = [
            Button(text="💨 Einweg Vapes", callback_data="cat_einweg"),
            Button(text="🔄 Pods & Köpfe", callback_data="cat_pods"),
            Button(text="💧 Liquids", callback_data="cat_liquid"),
            Button(text="🌿 Shisha & Tabak", callback_data="cat_tabak"),
            Button(text="🍫 Snacks", callback_data="cat_snacks"),
            Button(text="🥤 Drinks", callback_data="cat_drinks"),
        ]
        return BotResponse(
            text="Wir haben einiges am Start! Was interessiert dich? 👇",
            keyboards=[Keyboard(type=KeyboardType.CATEGORIES, buttons=buttons)],
        )
    else:
        # WhatsApp: Text-based categories
        return BotResponse(
            text=(
                "Wir haben einiges am Start! Was interessiert dich?\n\n"
                "💨 *Einweg Vapes* - Elf Bar, Flerbar, Lost Mary...\n"
                "🔄 *Pods & Köpfe* - ELFA, Tappo, Crystal...\n"
                "💧 *Liquids* - Bar Juice, Dinner Lady...\n"
                "🌿 *Shisha & Tabak* - Holster, Al Fakher...\n"
                "🍫 *Snacks* - Mr Beast, Candy...\n"
                "🥤 *Drinks* - Durstlöscher, Energy...\n\n"
                "Schreib einfach den Namen oder die Kategorie! 👍"
            )
        )


def handle_pending_quantity(session, text):
    """Handle manual quantity input when pending_selection is set."""
    pending = session["pending_selection"]
    quantity = int(text)
    vpe_num = int(pending.get("vpe", 1))
    pid = pending["product_id"]
    name = pending["name"]
    brand = pending.get("brand", "")
    price = pending.get("price", "")
    label = f"{brand} - {name}".strip(" -")

    round_hint = ""
    if vpe_num > 1 and quantity % vpe_num != 0:
        old_qty = quantity
        quantity = ((quantity // vpe_num) + 1) * vpe_num
        round_hint = f"📦 Wird in {vpe_num}er-Packs geliefert – ich pack dir {quantity} statt {old_qty} ein!\n\n"

    # Stock check
    product_check = cache.get_product_by_id(pid)
    if product_check:
        try:
            stock = int(product_check.get("stock", 0))
            if 0 < stock < quantity:
                old_qty = quantity
                if vpe_num > 1:
                    quantity = (stock // vpe_num) * vpe_num
                    if quantity == 0:
                        return BotResponse(
                            text=f"⚠️ Von {label} sind nur noch {stock} Stück auf Lager, aber die Mindestbestellung ist {vpe_num}. Leider nicht genug! 😕"
                        )
                else:
                    quantity = stock
                round_hint = f"⚠️ Nur noch {stock} auf Lager – ich pack dir {quantity} ein (statt {old_qty})!\n\n"
        except (ValueError, TypeError):
            pass

    # Add to cart
    existing = next((i for i in session["cart"] if str(i["product_id"]) == pid), None)
    if existing:
        existing["quantity"] += quantity
    else:
        img_url = ""
        if product_check:
            img_url = product_check.get("image_url", "")
            if not img_url and product_check.get("post_parent"):
                parent_p = cache.get_product_by_id(product_check["post_parent"])
                if parent_p:
                    img_url = parent_p.get("image_url", "")
        session["cart"].append({
            "product_id": pid, "title": label, "quantity": quantity,
            "price": str(price), "image_url": img_url,
        })

    session["conversation"].append({"role": "user", "content": f"{quantity}x {name}"})
    session["conversation"].append({"role": "assistant", "content": f"✅ {quantity}x {name} im Warenkorb!"})
    session["pending_selection"] = None

    n = len(session["cart"])
    cart_total = sum(parse_price(i.get("price", "0")) * i.get("quantity", 0) for i in session["cart"])
    shipping_hint = ""
    if 0 < cart_total < 1000:
        diff = 1000 - cart_total
        shipping_hint = f"\n💡 Noch {format_price_de(diff)} bis Gratisversand!"
    elif cart_total >= 1000:
        shipping_hint = "\n🚚 Gratisversand!"

    onboarding = get_hint(session, "first_cart_add")
    if not onboarding:
        onboarding = get_hint(session, "multi_order")

    from dp_connect_bot.models.response import WcAction
    return BotResponse(
        text=(
            f"{round_hint}"
            f"✅ {quantity}x {label} im Warenkorb!\n"
            f"💰 Gesamt: {format_price_de(cart_total)} netto ({n} Produkt{'e' if n > 1 else ''})"
            f"{shipping_hint}\n\n"
            f"Noch was dazu? Oder schreib *fertig* zum Bestellen 🛒"
            f"{onboarding}"
        ),
        wc_actions=[WcAction(action="add", product_id=pid, quantity=quantity)],
    )
