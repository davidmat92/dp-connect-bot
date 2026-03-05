"""
Unified message & callback handlers – single code path for ALL channels.
This replaces handle_message(), handle_callback(), webchat_send(), webchat_action().
"""

from dp_connect_bot.config import (
    CONFIRM_ALL, CHECKOUT_WORDS, CART_DISPLAY_WORDS,
    REORDER_TRIGGERS, BROWSE_TRIGGERS, CATEGORY_MAP, log,
)
from dp_connect_bot.models.response import BotResponse, Keyboard, KeyboardType, WcAction
from dp_connect_bot.models.session import session_manager
from dp_connect_bot.services.claude_ai import call_claude
from dp_connect_bot.services.product_context import build_product_context
from dp_connect_bot.services.product_cache import cache
from dp_connect_bot.services.cart_processing import (
    process_cart_actions, format_cart, format_cart_rich, generate_checkout_url,
)
from dp_connect_bot.services.history import (
    archive_session, update_daily_stats, track_event,
)
from dp_connect_bot.utils.formatting import (
    format_price_de, parse_price, get_variant_display_name,
)
from dp_connect_bot.utils.hints import get_hint
from dp_connect_bot.handlers.commands import (
    handle_start, handle_cart_display, handle_reset, handle_help,
)
from dp_connect_bot.handlers.support import handle_support_step, handle_support_message, handle_login_email
from dp_connect_bot.handlers.cart import (
    handle_checkout, handle_cart_view, handle_reorder,
    handle_browse, handle_pending_quantity,
)
from dp_connect_bot.handlers.mode import (
    detect_mode, handle_whatsapp_mode_choice, is_human_mode,
)


def unified_handle_message(chat_id, text, user_info=None, channel="telegram", wc_cart=None):
    """Single entry point for ALL text messages from ALL channels.

    Args:
        chat_id: Prefixed chat ID (e.g. "tg_123", "wa_49xxx", "web_abc")
        text: Message text
        user_info: Dict with user info (first_name, last_name, username, etc.)
        channel: "telegram", "whatsapp", or "web"
        wc_cart: WooCommerce cart from frontend (webchat only)

    Returns:
        BotResponse
    """
    chat_id = str(chat_id)
    session = session_manager.get(chat_id, archive_callback=archive_session)
    session["channel"] = channel

    # Store user info
    if user_info and not session["customer_name"]:
        name = user_info.get("first_name", "")
        if user_info.get("last_name"):
            name += f" {user_info['last_name']}"
        session["customer_name"] = name.strip()

    if user_info:
        info = session.setdefault("user_info", {})
        if channel == "telegram" and not info.get("tg_username"):
            info.update({
                "tg_username": user_info.get("username", ""),
                "tg_first_name": user_info.get("first_name", ""),
                "tg_last_name": user_info.get("last_name", ""),
                "tg_user_id": user_info.get("id", ""),
            })
        elif channel == "web":
            for key in ("wp_user_id", "wp_email", "wp_name"):
                if user_info.get(key):
                    info[key] = user_info[key]

    session["message_count"] = session.get("message_count", 0) + 1

    # Analytics
    update_daily_stats(channel)
    if session["message_count"] == 1:
        track_event("session_start", chat_id, channel)

    # --- Commands ---
    stripped = text.strip()
    if stripped.startswith("/start"):
        resp = handle_start(session)
        session_manager.save(chat_id, session)
        return resp

    if stripped.startswith("/warenkorb") or stripped.startswith("/cart"):
        resp = handle_cart_display(session)
        session_manager.save(chat_id, session)
        return resp

    if stripped.startswith("/reset"):
        resp = handle_reset(session)
        session_manager.save(chat_id, session)
        return resp

    if stripped.startswith("/hilfe") or stripped.startswith("/help"):
        return handle_help()

    # --- Callback-like strings from WhatsApp/channel button clicks ---
    # WhatsApp sends button IDs as regular text, route them to callback handler
    _CB_PREFIXES = ("mode_", "sel_", "qty_", "custom_", "cat_", "reorder_", "cb_", "login_", "done_")
    if stripped.startswith(_CB_PREFIXES) or stripped == "noop":
        resp = unified_handle_callback(chat_id, stripped, channel=channel)
        session_manager.save(chat_id, session)
        return resp

    # --- Pending selection (manual quantity input) ---
    pending = session.get("pending_selection")
    if pending and stripped.isdigit():
        resp = handle_pending_quantity(session, stripped)
        session_manager.save(chat_id, session)
        return resp

    # --- Mode gate ---
    mode_response = detect_mode(session, text, channel)
    if mode_response:
        session_manager.save(chat_id, session)
        return mode_response

    # WhatsApp mode choice ("1", "2" or "3" as text fallback)
    wa_mode = handle_whatsapp_mode_choice(session, text)
    if wa_mode:
        session_manager.save(chat_id, session)
        return wa_mode

    # --- Login-Hilfe Flow (email step) ---
    if session.get("mode") == "login_help" and session.get("login_step") == "ask_email":
        resp = handle_login_email(session, text)
        session_manager.save(chat_id, session)
        return resp

    # --- Support flow (legacy step handler – now always returns None) ---
    support_resp = handle_support_step(session, text, channel)
    if support_resp:
        session_manager.save(chat_id, session)
        return support_resp

    # --- AI-First Support ---
    if session.get("mode") == "support" and not session.get("human_mode"):
        resp = handle_support_message(chat_id, text, session, channel)
        session_manager.save(chat_id, session)
        return resp

    # --- Human takeover ---
    if is_human_mode(session):
        lower = text.strip().lower()
        order_intents = {"bestellen", "bestell", "möchte bestellen", "möchte was bestellen",
                         "ich will bestellen", "kann ich bestellen", "order", "produkte",
                         "was habt ihr", "elf bar", "lost mary", "pods", "snacks", "drinks"}
        if lower in order_intents or stripped.startswith("/start"):
            session["human_mode"] = False
            session["mode"] = "order"
            session_manager.save(chat_id, session)
            return BotResponse(text="🛒 Klar! Bestell-Modus ist aktiv. Was brauchst du?")
        session["conversation"].append({"role": "user", "content": text})
        session_manager.save(chat_id, session)
        return BotResponse(
            text="💬 Deine Nachricht wurde weitergeleitet. Ein Mitarbeiter antwortet dir gleich!\n\nWenn du bestellen möchtest, schreib einfach /start"
        )

    lower_text = text.strip().lower()

    # --- Checkout shortcut ---
    if lower_text in CHECKOUT_WORDS and session.get("cart"):
        resp = handle_checkout(session, channel)
        if resp:
            track_event("checkout", chat_id, channel)
            session_manager.save(chat_id, session)
            return resp

    # --- Cart display shortcut ---
    if lower_text in CART_DISPLAY_WORDS:
        resp = handle_cart_view(session)
        session_manager.save(chat_id, session)
        return resp

    # --- Reorder shortcut ---
    if lower_text in REORDER_TRIGGERS:
        resp = handle_reorder(session, channel)
        if resp:
            session_manager.save(chat_id, session)
            return resp
        # No last_order → fall through to Claude

    # --- Browse/categories shortcut ---
    if lower_text in BROWSE_TRIGGERS:
        resp = handle_browse(session, channel)
        session_manager.save(chat_id, session)
        return resp

    # --- AI Response ---
    if lower_text in CONFIRM_ALL:
        product_context = ""
    else:
        product_context = build_product_context(text)

    ai_response = call_claude(session, text, product_context, wc_cart=wc_cart)
    clean_text, keyboards, wc_actions = process_cart_actions(session, ai_response)

    session_manager.save(chat_id, session)

    return BotResponse(
        text=clean_text,
        keyboards=keyboards,
        wc_actions=wc_actions,
        cart=session.get("cart", []),
        cart_rich=format_cart_rich(session),
    )


def unified_handle_callback(chat_id, callback_data, channel="telegram"):
    """Single entry point for ALL callback/button clicks from ALL channels.

    Args:
        chat_id: Prefixed chat ID
        callback_data: Callback data string (e.g. "sel_3011", "qty_3011_50")
        channel: "telegram", "whatsapp", or "web"

    Returns:
        BotResponse
    """
    chat_id = str(chat_id)
    session = session_manager.get(chat_id, archive_callback=archive_session)

    if callback_data == "mode_order":
        session["mode"] = "order"
        session["human_mode"] = False
        session["support_step"] = None
        voice_hint = get_hint(session, "voice_available") if channel == "whatsapp" else ""
        session_manager.save(chat_id, session)
        return BotResponse(
            text=(
                "🛒 *Bestell-Assistent* aktiv!\n\n"
                "Sag mir einfach was du brauchst, z.B.:\n"
                "• \"Elf Bar 800\"\n"
                "• \"Was habt ihr an Pods?\"\n"
                "• \"50 Cherry und 30 Peach\"\n\n"
                f"Was darf's sein? 🚀{voice_hint}"
            ),
            answer_callback_text="🛒 Bestell-Modus!",
        )

    elif callback_data == "mode_support":
        session["mode"] = "support"
        session["support_step"] = None
        session_manager.save(chat_id, session)
        return BotResponse(
            text=(
                "🎧 *Kundenservice*\n\n"
                "Klar, wie kann ich dir helfen? Beschreib mir einfach dein Anliegen – "
                "ich kann z.B. Bestellungen nachschlagen, Account-Probleme loesen "
                "oder dich an Davides Team weiterleiten. ✍️"
            ),
            answer_callback_text="🎧 Kundenservice!",
        )

    elif callback_data == "mode_login":
        session["mode"] = "login_help"
        session["login_step"] = "ask_email"
        session_manager.save(chat_id, session)
        return BotResponse(
            text=(
                "🔑 *Login-Hilfe*\n\n"
                "Klar! Was ist deine E-Mail-Adresse, mit der du registriert bist? ✉️"
            ),
            answer_callback_text="🔑 Login-Hilfe!",
        )

    elif callback_data == "login_magic":
        return _handle_login_magic(session, chat_id)

    elif callback_data == "login_newpw":
        return _handle_login_newpw(session, chat_id)

    elif callback_data == "login_register":
        session["mode"] = None
        session["login_step"] = None
        session_manager.save(chat_id, session)
        return BotResponse(
            text=(
                "📝 *Jetzt registrieren!*\n\n"
                "Erstelle dir hier deinen Account:\n"
                "👉 https://dpconnect.de/kunde-werden/\n\n"
                "Nach der Registrierung bekommst du deine Zugangsdaten per E-Mail. 📧"
            ),
            keyboards=[Keyboard(type=KeyboardType.MODE_CHOICE)],
            answer_callback_text="📝 Registrierung!",
        )

    elif callback_data == "login_retry":
        session["login_step"] = "ask_email"
        session_manager.save(chat_id, session)
        return BotResponse(
            text="Kein Problem! Gib mir eine andere E-Mail-Adresse: ✉️",
            answer_callback_text="🔄 Nochmal versuchen",
        )

    elif callback_data.startswith("sel_"):
        return _handle_flavor_selection(session, chat_id, callback_data)

    elif callback_data.startswith("qty_"):
        return _handle_quantity_selection(session, chat_id, callback_data)

    elif callback_data.startswith("custom_"):
        return _handle_custom_quantity(session, chat_id, callback_data)

    elif callback_data == "done_flavors":
        return _handle_done_flavors(session, chat_id)

    elif callback_data == "noop":
        return BotResponse(is_silent=True, answer_callback_text="")

    elif callback_data.startswith("cat_"):
        cat_text = CATEGORY_MAP.get(callback_data, "Produkte")
        session["mode"] = "order"
        session_manager.save(chat_id, session)
        return unified_handle_message(chat_id, cat_text, channel=session.get("channel", "telegram"))

    elif callback_data == "reorder_yes" or callback_data == "reorder_last":
        return _handle_reorder_confirm(session, chat_id)

    elif callback_data == "reorder_no":
        session_manager.save(chat_id, session)
        return BotResponse(
            text="Kein Problem! Was darf's stattdessen sein? 😊",
            answer_callback_text="👍",
        )

    elif callback_data.startswith("cb_"):
        return _handle_callback_request(session, chat_id, callback_data)

    return BotResponse(is_silent=True)


def _handle_flavor_selection(session, chat_id, callback_data):
    """Handle flavor/variant button click."""
    product_id = callback_data[4:]
    product = cache.get_product_by_id(product_id)

    if not product:
        return BotResponse(answer_callback_text="Produkt nicht gefunden")

    name = get_variant_display_name(product)
    price = format_price_de(product.get("price"))
    vpe = product.get("vpe", "1")

    session["pending_selection"] = {
        "product_id": product_id,
        "name": name,
        "brand": product.get("brand", ""),
        "price": product.get("price", ""),
        "vpe": vpe,
    }

    try:
        vpe_num = int(vpe)
    except (ValueError, TypeError):
        vpe_num = 1
    vpe_hint = f"Mindestbestellung: {vpe_num} Stück" if vpe_num > 1 else "Ab 1 Stück bestellbar"

    session_manager.save(chat_id, session)

    return BotResponse(
        text=f"👍 *{name}* - {price} pro Stück\n{vpe_hint} | Wie viele?",
        keyboards=[Keyboard(
            type=KeyboardType.QUANTITIES,
            product_id=product_id,
            label=name,
            price=str(product.get("price", "")),
            vpe=vpe,
        )],
        answer_callback_text=f"✅ {name}",
    )


def _handle_quantity_selection(session, chat_id, callback_data):
    """Handle quantity button click."""
    parts = callback_data.split("_")
    if len(parts) != 3:
        return BotResponse(is_silent=True)

    product_id = parts[1]
    quantity = int(parts[2])

    product = cache.get_product_by_id(product_id)
    if not product:
        return BotResponse(answer_callback_text="Nicht mehr verfügbar")

    name = get_variant_display_name(product)
    price = product.get("price", "")
    brand = product.get("brand", "")

    # Add to cart
    existing = next((i for i in session["cart"] if str(i["product_id"]) == product_id), None)
    if existing:
        existing["quantity"] += quantity
        total_qty = existing["quantity"]
    else:
        img_url = product.get("image_url", "")
        if not img_url and product.get("post_parent"):
            parent_p = cache.get_product_by_id(product["post_parent"])
            if parent_p:
                img_url = parent_p.get("image_url", "")
        session["cart"].append({
            "product_id": product_id,
            "title": f"{brand} - {name}".strip(" -"),
            "quantity": quantity,
            "price": str(price),
            "image_url": img_url,
        })
        total_qty = quantity

    session["conversation"].append({"role": "user", "content": f"[Button geklickt: {quantity}x {name}]"})
    session["conversation"].append({"role": "assistant", "content": f"✅ {quantity}x {name} im Warenkorb!"})
    session["pending_selection"] = None

    n = len(session["cart"])
    cart_total = sum(parse_price(i.get("price", "0")) * i.get("quantity", 0) for i in session["cart"])

    # Show flavor keyboard again if parent exists
    parent_id = product.get("post_parent")
    keyboards = []
    show_flavors_again = False
    if parent_id and parent_id != "0":
        show_flavors_again = True
        keyboards.append(Keyboard(type=KeyboardType.FLAVORS, parent_id=parent_id))

    shipping_hint = ""
    if 0 < cart_total < 1000:
        diff = 1000 - cart_total
        shipping_hint = f"\n💡 Noch {format_price_de(diff)} bis Gratisversand!"
    elif cart_total >= 1000:
        shipping_hint = "\n🚚 Gratisversand!"

    onboarding = get_hint(session, "first_cart_add")
    if not onboarding:
        onboarding = get_hint(session, "multi_order")

    if show_flavors_again:
        text = (
            f"✅ *{quantity}x {name}* im Warenkorb! ({n} Produkt{'e' if n > 1 else ''})\n\n"
            f"Noch einen Geschmack dazu? 👇"
        )
    else:
        text = (
            f"✅ *{quantity}x {brand} - {name}* im Warenkorb!\n"
            f"💰 Gesamt: {format_price_de(cart_total)} netto ({n} Produkt{'e' if n > 1 else ''})"
            f"{shipping_hint}\n\n"
            f"Noch was dazu? Oder schreib *fertig* zum Bestellen 🛒"
            f"{onboarding}"
        )

    wc_actions = [WcAction(action="add", product_id=product_id, quantity=quantity)]

    session_manager.save(chat_id, session)

    return BotResponse(
        text=text,
        keyboards=keyboards,
        wc_actions=wc_actions,
        answer_callback_text=f"✅ {quantity}x hinzugefügt!",
        cart=session.get("cart", []),
        cart_rich=format_cart_rich(session),
    )


def _handle_custom_quantity(session, chat_id, callback_data):
    """Handle 'custom quantity' button click."""
    product_id = callback_data[7:]
    product = cache.get_product_by_id(product_id)

    if not product:
        return BotResponse(answer_callback_text="Produkt nicht gefunden")

    name = get_variant_display_name(product)
    brand = product.get("brand", "")
    vpe = product.get("vpe", "1")
    label = f"{brand} - {name}".strip(" -")

    session["pending_selection"] = {
        "product_id": product_id,
        "name": name,
        "brand": brand,
        "price": product.get("price", ""),
        "vpe": vpe,
    }
    session_manager.save(chat_id, session)

    return BotResponse(
        text=f"Schreib mir die Menge für *{label}* (VPE: {vpe}):",
        answer_callback_text="Menge eingeben",
    )


def _handle_done_flavors(session, chat_id):
    """Handle 'done with flavors' button."""
    n = len(session.get("cart", []))
    if n > 0:
        total = sum(parse_price(i.get("price", "0")) * i.get("quantity", 0) for i in session["cart"])
        msg = f"👍 Alles klar! {n} Produkt{'e' if n > 1 else ''} im Warenkorb.\n💰 Gesamt: {format_price_de(total)} netto\n\n"
        if total < 1000:
            diff = 1000 - total
            msg += f"💡 Noch {format_price_de(diff)} bis Gratisversand!\n\n"
        msg += "Noch was anderes? Oder schreib *fertig* zum Bestellen 🛒"
    else:
        msg = "Was darf's als nächstes sein? 😊"

    session_manager.save(chat_id, session)
    return BotResponse(text=msg, answer_callback_text="👍")


def _handle_reorder_confirm(session, chat_id):
    """Handle reorder confirmation."""
    last_order = session.get("last_order", [])
    if last_order:
        session["cart"] = [dict(i) for i in last_order]
        n = len(session["cart"])
        total = sum(parse_price(i.get("price", "0")) * i.get("quantity", 0) for i in session["cart"])
        cart_summary = "\n".join(f"  ✅ {i['quantity']}x {i['title']}" for i in session["cart"])
        session["conversation"].append({"role": "user", "content": "[Nachbestellung: Letzte Bestellung geladen]"})
        session["conversation"].append({"role": "assistant", "content": f"🔄 Letzte Bestellung geladen:\n{cart_summary}"})

        wc_actions = [
            WcAction(action="add", product_id=i["product_id"], quantity=i["quantity"])
            for i in session["cart"]
        ]

        session_manager.save(chat_id, session)
        return BotResponse(
            text=(
                f"🔄 *Letzte Bestellung geladen!*\n\n"
                f"{cart_summary}\n\n"
                f"💰 Gesamt: {format_price_de(total)} netto\n\n"
                f"Alles so lassen? Schreib *fertig* zum Bestellen oder sag mir was du ändern willst! ✏️"
            ),
            answer_callback_text="🔄 Letzte Bestellung geladen!",
            wc_actions=wc_actions,
            cart=session.get("cart", []),
            cart_rich=format_cart_rich(session),
        )
    else:
        session_manager.save(chat_id, session)
        return BotResponse(
            text="Hab leider keine vorherige Bestellung gespeichert. Sag mir einfach was du brauchst! 😊",
            answer_callback_text="Keine letzte Bestellung gefunden",
        )


def _handle_callback_request(session, chat_id, callback_data):
    """Handle callback/contact request buttons."""
    contact_type = callback_data[3:]  # email, phone, whatsapp, call
    channel = session.get("channel", "telegram")

    if contact_type == "whatsapp":
        session["conversation"].append({"role": "user", "content": "[Rückruf gewählt: whatsapp]"})
        track_event("callback_requested", chat_id, channel, "whatsapp")
        session_manager.save(chat_id, session)
        return BotResponse(
            text=(
                "💬 Top! Schreib uns direkt bei WhatsApp:\n\n"
                "👉 https://wa.me/4915906192252\n\n"
                "Da meldet sich dann jemand aus Davides Team. 👋"
            ),
            answer_callback_text="✅ Weitergeleitet!",
        )
    elif contact_type in ("email", "phone", "call"):
        session["mode"] = "support"
        session["support_step"] = None
        session_manager.save(chat_id, session)
        return BotResponse(
            text=(
                "📧 Klar! Beschreib mir dein Anliegen – ich versuche dir direkt zu helfen. "
                "Falls noetig, leite ich dich an Davides Team weiter. ✍️"
                if contact_type == "email"
                else "📞 Klar! Beschreib mir dein Anliegen – ich versuche dir direkt zu helfen. "
                "Falls noetig, leite ich dich an Davides Team weiter. ✍️"
            ),
            answer_callback_text="✅ Support aktiv!",
        )

    return BotResponse(is_silent=True)


# ============================================================
# LOGIN FLOW HELPERS
# ============================================================

def _handle_login_magic(session, chat_id):
    """Handle Magic Login button click."""
    session["mode"] = None
    session["login_step"] = None
    session_manager.save(chat_id, session)

    return BotResponse(
        text=(
            "🔑 *Magic Login*\n\n"
            "Klicke auf diesen Link und gib deine E-Mail-Adresse ein:\n"
            "👉 https://dpconnect.de/anmelden/?action=magic_login\n\n"
            "Du bekommst dann einen Einmal-Link per E-Mail, mit dem du dich "
            "direkt einloggen kannst – ganz ohne Passwort! 🪄\n\n"
            "Danach kannst du in deinem Konto ein neues Passwort setzen."
        ),
        keyboards=[Keyboard(type=KeyboardType.MODE_CHOICE)],
        answer_callback_text="🔑 Magic Login!",
    )


def _handle_login_newpw(session, chat_id):
    """Handle 'Neues Passwort' button click."""
    from dp_connect_bot.services.woocommerce import wc_client

    email = session.get("login_email", "")
    if not email:
        session_manager.save(chat_id, session)
        return BotResponse(
            text="Da ist was schiefgelaufen. Versuch's nochmal mit /start 🔄",
            answer_callback_text="❌ Fehler",
        )

    result = wc_client.send_new_password(email)

    session["mode"] = None
    session["login_step"] = None
    session_manager.save(chat_id, session)

    if result.get("success"):
        return BotResponse(
            text=(
                "✅ *Neues Passwort versendet!*\n\n"
                f"Ein neues Passwort wurde an *{email}* gesendet. "
                "Check deine E-Mails (auch den Spam-Ordner). 📧\n\n"
                "Du kannst dich dann hier einloggen:\n"
                "👉 https://dpconnect.de/anmelden/\n\n"
                "Tipp: Aendere das Passwort nach dem Login in deinem Kontobereich! 🔒"
            ),
            keyboards=[Keyboard(type=KeyboardType.MODE_CHOICE)],
            answer_callback_text="✅ Passwort gesendet!",
        )
    else:
        error = result.get("error", "Unbekannter Fehler")
        return BotResponse(
            text=(
                f"❌ Das hat leider nicht geklappt: {error}\n\n"
                "Versuch es alternativ mit dem Magic Login:\n"
                "👉 https://dpconnect.de/anmelden/?action=magic_login\n\n"
                "Oder kontaktiere uns: +49 221 650 878 78"
            ),
            keyboards=[Keyboard(type=KeyboardType.MODE_CHOICE)],
            answer_callback_text="❌ Fehler",
        )
