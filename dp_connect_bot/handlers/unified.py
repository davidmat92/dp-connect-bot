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
    handle_browse, handle_pending_quantity, extract_quantity_answer,
)
from dp_connect_bot.handlers.mode import (
    detect_mode, handle_whatsapp_mode_choice, is_human_mode,
)


def _detect_lang(text):
    """Grobe Sprach-Erkennung der Kundennachricht. Gibt 'de'|'tr'|'ar'|'ru'|'en'
    oder None (zu kurz/unklar → bisherige Sprache behalten) zurueck. Bewusst leicht-
    gewichtig — die KI spiegelt die Sprache eh; das hier steuert nur, ob hartcodierte
    DEUTSCHE Hinweis-Anhaenge angehaengt werden (nicht in einen tuerkischen Chat)."""
    if not text or len(text.strip()) < 3:
        return None
    if any('؀' <= c <= 'ۿ' for c in text):
        return "ar"
    if any('Ѐ' <= c <= 'ӿ' for c in text):
        return "ru"
    low = " " + text.lower() + " "
    if any(c in low for c in "şğıİ") or any(w in low for w in (
            " var ", " mı", " mi ", " merhaba", " selam", " fiyat", " lazım",
            " istiyorum", " teşekkür", " kaç ", " adet")):
        return "tr"
    en = sum(1 for w in (" the ", " do you ", " have ", " hi ", " hello ", " price ",
                         " need ", " want ", " please ", " in stock ", " how much ",
                         " thanks ", " available ", " your ") if w in low)
    de = sum(1 for w in (" habt ", " ihr ", " ich ", " brauche ", " hallo ", " servus ",
                         " preis ", " haben ", " und ", " der ", " die ", " das ",
                         " bestellen ", " moin ", " danke ", " noch ", " bitte ") if w in low)
    if en >= 1 and en > de:
        return "en"
    if de >= 1:
        return "de"
    return None


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

    # Kanal komplett deaktiviert? → Hinweis statt Verarbeitung
    from dp_connect_bot.services.bot_config import get_channel_config
    ch_cfg = get_channel_config(channel)
    if not ch_cfg["enabled"]:
        return BotResponse(text=ch_cfg["disabled_message"])

    # Geheimes Reset-Keyword (Tests): Session loeschen, frisch starten
    from dp_connect_bot.config import BOT_RESET_KEYWORD
    if BOT_RESET_KEYWORD and text.strip().lower() == BOT_RESET_KEYWORD.lower():
        session_manager.delete(chat_id)
        log.info(f"[{channel}:{chat_id}] Session per Reset-Keyword geloescht")
        return BotResponse(
            text="🔄 Session zurückgesetzt! Schreib mir einfach, dann starten wir von vorne."
        )

    session = session_manager.get(chat_id, archive_callback=archive_session)
    session["channel"] = channel
    # Sprache des Kunden grob erkennen (fuer sprach-sensible Hinweise). Bei kurzen/
    # unklaren Nachrichten bleibt die bisherige Sprache erhalten. Die KI selbst
    # spiegelt die Sprache ohnehin (Prompt-Regel) — das hier steuert nur die
    # hartcodierten deutschen Hinweis-Anhaenge.
    _l = _detect_lang(text)
    if _l:
        session["lang"] = _l

    # --- B2B-Verifizierung (Preise nur fuer registrierte Kunden) ---
    from dp_connect_bot.services import verification as verif
    if verif.enabled() and not session.get("verified"):
        # Frueher verifiziert? (ueberlebt Session-Ablauf — wichtig fuer Telegram)
        stored = verif.get_stored_verification(chat_id)
        if stored and stored.get("customer_id"):
            session["verified"] = stored
            if stored.get("name") and not session.get("customer_name"):
                session["customer_name"] = stored["name"]
    # Selbstheilung: Verifizierungen ohne customer_id (Alt-Bug) neu matchen,
    # sonst wird der Chat-Checkout nicht angeboten
    if verif.enabled() and channel == "whatsapp":
        v = session.get("verified")
        if v and not v.get("customer_id"):
            result = verif.lookup_phone(chat_id.replace("wa_", ""))
            if result.get("found"):
                verif.mark_verified(session, result["customer"], chat_id=chat_id)
                log.info(f"[{channel}:{chat_id}] Verifizierung selbstgeheilt (Kunde {result['customer'].get('id')})")
    if verif.enabled() and not session.get("verified"):
        # WhatsApp: verifizierte Absendernummer automatisch matchen (einmalig)
        if channel == "whatsapp" and not session.get("verify_phone_checked"):
            result = verif.lookup_phone(chat_id.replace("wa_", ""))
            if result.get("found"):
                verif.mark_verified(session, result["customer"], chat_id=chat_id)
                log.info(f"[{channel}:{chat_id}] Auto-verifiziert via Telefon-Match (Kunde {result['customer'].get('id')})")
            if not result.get("error"):
                session["verify_phone_checked"] = True

        # E-Mail-Code-Flow — nur im Bestell-Kontext, nicht im Support
        # (dort fragt der Support-Bot selbst nach E-Mails)
        if not session.get("verified") and session.get("mode") in (None, "order", "choosing"):
            code_match = verif.CODE_RE.match(text)
            if session.get("verify_pending_email") and code_match:
                # Lokales Rate-Limit gegen Code-Brute-Force (zusaetzlich zum
                # dp-tools-Limit pro Code): max 8 Eingaben / 10 Min / Session.
                import time as _vt
                _now = _vt.time()
                _att = [t for t in session.get("verify_code_attempts", []) if _now - t < 600]
                if len(_att) >= 8:
                    session["verify_code_attempts"] = _att
                    session.pop("verify_pending_email", None)
                    session_manager.save(chat_id, session)
                    return BotResponse(text="Zu viele Code-Versuche. ⏳ Bitte fordere gleich einen neuen Code an — schick mir dafür nochmal deine E-Mail-Adresse.")
                _att.append(_now)
                session["verify_code_attempts"] = _att
                res = verif.check_code(session["verify_pending_email"], code_match.group(1))
                if res.get("valid"):
                    verif.mark_verified(session, res["customer"], chat_id=chat_id)
                    session["mode"] = "order"
                    name = res["customer"].get("name", "")
                    session_manager.save(chat_id, session)
                    return BotResponse(
                        text=(f"✅ Verifiziert{', ' + name if name else ''}! 🎉\n\n"
                              "Ab jetzt siehst du alle Preise und kannst direkt bestellen. "
                              "Was brauchst du? 🛒")
                    )
                reason = res.get("reason", "")
                session_manager.save(chat_id, session)
                if reason in ("expired_or_missing", "too_many_attempts"):
                    session.pop("verify_pending_email", None)
                    return BotResponse(
                        text="⏰ Der Code ist abgelaufen. Schick mir einfach nochmal deine E-Mail-Adresse, dann bekommst du einen neuen."
                    )
                return BotResponse(text="❌ Der Code stimmt leider nicht. Schau nochmal in die E-Mail und probier's erneut!")

            email_match = verif.EMAIL_RE.search(text)
            if email_match and len(text.strip()) < 60:
                email = email_match.group(0).lower()
                # Rate-Limit gegen Missbrauch: ohne Bremse koennte der Bot
                # benutzt werden, um fremden Kunden Verifizierungs-Mails zuzuspammen
                # ODER zu testen, welche E-Mails Kunden sind (Enumeration via
                # "Code geschickt" vs "kein Konto"). Max 5 Anfragen / 10 Min / Session.
                import time as _vt
                _now = _vt.time()
                _reqs = [t for t in session.get("verify_code_requests", []) if _now - t < 600]
                if len(_reqs) >= 5:
                    session["verify_code_requests"] = _reqs
                    session_manager.save(chat_id, session)
                    return BotResponse(text="Du hast gerade zu viele Verifizierungs-Codes angefordert. ⏳ Bitte warte ein paar Minuten und versuch es dann nochmal.")
                _reqs.append(_now)
                session["verify_code_requests"] = _reqs
                res = verif.send_code(email)
                session_manager.save(chat_id, session)
                if res.get("sent"):
                    session["verify_pending_email"] = email
                    session_manager.save(chat_id, session)
                    return BotResponse(
                        text=(f"📧 Ich hab dir einen 6-stelligen Code an *{email}* geschickt!\n\n"
                              "Tipp ihn einfach hier ein, dann bist du verifiziert. "
                              "(Schau ggf. auch im Spam-Ordner)")
                    )
                if res.get("inactive"):
                    return BotResponse(
                        text=("Dein Account ist noch nicht freigeschaltet — du bekommst eine E-Mail, "
                              "sobald es so weit ist! Meld dich gern bei Davides Team, falls es eilt. 🙏")
                    )
                if res.get("error"):
                    return BotResponse(text="Da hat technisch was geklemmt — probier's gleich nochmal! 🙏")
                return BotResponse(
                    text=(f"Mit *{email}* finde ich leider kein Kundenkonto. 🤔\n\n"
                          "Vielleicht eine andere E-Mail? Oder noch kein Kunde? "
                          "Registrier dich kostenlos: https://dpconnect.de/kunde-werden/ — "
                          "nach der Freischaltung siehst du alle Preise!")
                )

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

    if stripped.startswith("/hilfe") or stripped.startswith("/help") or stripped.startswith("/anleitung"):
        from dp_connect_bot.handlers.commands import handle_anleitung
        return handle_anleitung(channel)

    # Stichwort "Anleitung" — jederzeit abrufbar (alle Kanaele)
    if stripped.lower() in ("anleitung", "anleitung bitte", "zeig mir die anleitung", "was kannst du", "was kannst du alles"):
        from dp_connect_bot.handlers.commands import handle_anleitung
        session["anleitung_offered"] = True
        session_manager.save(chat_id, session)
        return handle_anleitung(channel)

    # --- Callback-like strings from WhatsApp/channel button clicks ---
    # WhatsApp sends button IDs as regular text, route them to callback handler
    # WhatsApp liefert Button-Klicks als Text (die callback_data-ID) — ALLE
    # Keyboard-Prefixe muessen hier stehen, sonst landet der Klick bei der KI
    # statt im Callback-Handler. "chatorder_" fehlte → Chat-Bestellung per
    # Rechnung/Vorkasse loeste auf WhatsApp nichts aus.
    _CB_PREFIXES = ("mode_", "sel_", "qty_", "custom_", "cat_", "reorder_",
                    "cb_", "login_", "done_", "chatorder_", "flavmore_")
    if stripped.startswith(_CB_PREFIXES) or stripped == "noop":
        resp = unified_handle_callback(chat_id, stripped, channel=channel)
        session_manager.save(chat_id, session)
        return resp

    # --- Human takeover (MUSS vor detect_mode/Login/Support stehen) ---
    # Hat ein Mitarbeiter uebernommen, darf KEIN anderer Handler die Nachricht
    # abfangen — sonst bekommt der Kunde Bot-Antworten (z.B. Login-/Support-Prompt)
    # statt der Weiterleitung. Der Ausstiegs-Button (mode_order) lief schon oben
    # durch den Callback-Gate und beendet human_mode; /start ebenso (Command oben).
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
        # Klar erkennbarer Ausstieg: ein Button (mode_order → human_mode=False) gilt
        # auf ALLEN Kanaelen (auch Webchat) und ist eindeutig — anders als der exakte
        # Stichwort-Abgleich, der einen Kunden sonst dauerhaft in der Weiterleitung
        # festhaelt, ohne ein laufendes Mitarbeiter-Gespraech zu kapern.
        return BotResponse(
            text=("💬 Deine Nachricht wurde weitergeleitet — ein Mitarbeiter meldet sich gleich!\n\n"
                  "Wenn du in der Zwischenzeit selbst weiter bestellen willst, tippe unten auf "
                  "*🛒 Bestellen* (oder schreib /start)."),
            keyboards=[Keyboard(type=KeyboardType.MODE_CHOICE)],
        )

    # --- Pending selection (manual quantity input) ---
    # Mengen-Antwort robust lesen: nicht nur "50", sondern auch "50 stück", "50x",
    # "ca. 50". Eine Frage mit eingebetteter Zahl ("habt ihr elf bar 600?") liefert
    # None → faellt korrekt durch zur KI (kapert NICHT die Mengen-Eingabe).
    pending = session.get("pending_selection")
    if pending and extract_quantity_answer(stripped) is not None:
        resp = handle_pending_quantity(session, stripped)
        session_manager.save(chat_id, session)
        return resp
    if pending and any(w in stripped.lower() for w in
                       ("nicht", "doch nicht", "abbrechen", "stop", "stopp", "cancel", "nein", "ne ")):
        # Kunde lehnt die angebotene Auswahl ab → Pending verwerfen,
        # Nachricht normal weiterverarbeiten (AI antwortet auf den Rest)
        session["pending_selection"] = None

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

    # --- Login-Hilfe Flow ---
    if session.get("mode") == "login_help":
        if session.get("login_step") == "ask_email":
            resp = handle_login_email(session, text)
            session_manager.save(chat_id, session)
            return resp
        # User typed text during button-selection step → remind them
        session_manager.save(chat_id, session)
        return BotResponse(
            text="Bitte wähle eine der Optionen oben oder schreib /start für ein neues Gespräch. 👆"
        )

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

    lower_text = text.strip().lower()

    # --- Checkout shortcut ---
    _checkout_intent = lower_text in CHECKOUT_WORDS or any(
        w in lower_text.split() for w in ("kasse", "checkout", "bestellen", "abschließen", "abschliessen")
    )
    # NUR ein REINER Checkout-Wunsch darf den Shortcut nehmen. Eine Nachricht
    # mit konkretem Bestell-Inhalt ("20 elf bar 800 blueberry bestellen") traegt
    # zwar "bestellen", ist aber eine Bestellung → muss zum AI-Flow, der das
    # Produkt einpackt. Sonst bekaeme der Kunde faelschlich "Warenkorb ist leer".
    _pure_checkout = (
        lower_text in CHECKOUT_WORDS
        or (_checkout_intent and len(lower_text.split()) <= 3
            and not any(c.isdigit() for c in lower_text))
    )
    # Bei aktiver Chat-Direktbestellung den Shortcut NICHT nehmen — sonst
    # umgeht der Standard-Link die Zahlart-Buttons. AI-Flow baut den Checkout.
    from dp_connect_bot.services.bot_config import load_bot_config
    _chat_checkout = (load_bot_config().get("chat_checkout_enabled")
                      and (session.get("verified") or {}).get("customer_id"))
    if _pure_checkout and session.get("cart") and not _chat_checkout:
        resp = handle_checkout(session, channel)
        if resp:
            track_event("checkout", chat_id, channel)
            session_manager.save(chat_id, session)
            return resp
    # Checkout-Wunsch bei LEEREM Warenkorb → freundlich statt Modus-Menue
    if _pure_checkout and not session.get("cart"):
        session["mode"] = "order"
        session_manager.save(chat_id, session)
        return BotResponse(
            text="Dein Warenkorb ist noch leer! 🛒 Sag mir einfach, was du brauchst — z.B. \"20 Elf Bar 800 Cherry\" — dann pack ich's ein und wir gehen zur Kasse. 😊"
        )

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

    # --- Kosten-/Spamschutz vor dem AI-Call ---
    # Tageslimit pro Session: unverifiziert knapp, verifiziert grosszuegig
    from datetime import date as _date
    today = _date.today().isoformat()
    if session.get("ai_quota_date") != today:
        session["ai_quota_date"] = today
        session["ai_quota_used"] = 0
    quota = 200 if verif.is_verified(session) else 30
    if session.get("ai_quota_used", 0) >= quota:
        session_manager.save(chat_id, session)
        log.warning(f"[{channel}:{chat_id}] Tageslimit erreicht ({quota})")
        return BotResponse(
            text=("Wow, das war heute ganz schön viel! 😅 Mein Tageslimit für dieses "
                  "Gespräch ist erreicht — morgen geht's normal weiter. "
                  "Dringend? Davides Team: 📞 +49 221 650 878 78")
        )
    session["ai_quota_used"] = session.get("ai_quota_used", 0) + 1
    # Ueberlange Nachrichten kappen (Kontext-/Kostenschutz)
    if len(text) > 2000:
        text = text[:2000]

    # --- AI Response ---
    if lower_text in CONFIRM_ALL:
        product_context = ""
    else:
        product_context = build_product_context(text)

    # Unverifizierte Kontakte sehen keine Preise (harter Filter, nicht nur Prompt)
    if not verif.is_verified(session):
        product_context = verif.strip_prices(product_context)

    ai_response = call_claude(session, text, product_context, wc_cart=wc_cart)
    clean_text, keyboards, wc_actions = process_cart_actions(session, ai_response)

    # Hartcodierte deutsche Hinweis-Anhaenge NUR fuer deutschsprachige Chats —
    # sonst klebt deutscher Text unter einer tuerkischen/arabischen Bot-Antwort.
    # (Die Anleitung selbst ist deutsch, daher fuer Nicht-Deutsch weggelassen.)
    _de = session.get("lang", "de") == "de"

    # Unverifizierte Telegram-Nutzer: Nummer-teilen-Button anbieten (einmalig).
    # Der Button bleibt sprachunabhaengig (wichtig fuer Verifizierung), nur der
    # deutsche Tipp-Text entfaellt bei Nicht-Deutsch.
    if (channel == "telegram" and not verif.is_verified(session)
            and not session.get("contact_button_shown")):
        session["contact_button_shown"] = True
        if _de:
            clean_text += "\n\n📱 Tipp: Teil einfach deine Nummer über den Button — geht am schnellsten!"
        keyboards = list(keyboards) + [Keyboard(type=KeyboardType.CONTACT_REQUEST)]

    # Anleitung anbieten: beim Erstkontakt im Bestell-Modus fragen,
    # spaeter gelegentlich dezent erinnern
    if session.get("mode") == "order" and not session.get("anleitung_offered"):
        session["anleitung_offered"] = True
        if _de:
            clean_text += ("\n\n👋 Schreibst du zum ersten Mal mit mir? "
                           "Soll ich dir kurz zeigen, was ich alles kann? "
                           "Schreib einfach *Anleitung* 📖")
    elif _de and session.get("mode") == "order" and session.get("message_count", 0) in (15, 45) \
            and not session.get(f"anleitung_hint_{session.get('message_count', 0)}"):
        session[f"anleitung_hint_{session.get('message_count', 0)}"] = True
        clean_text += "\n\n💡 _Tipp: Mit dem Stichwort *Anleitung* zeig ich dir alle meine Funktionen._"

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

    # Kanal komplett deaktiviert? → Hinweis statt Verarbeitung
    from dp_connect_bot.services.bot_config import get_channel_config
    ch_cfg = get_channel_config(channel)
    if not ch_cfg["enabled"]:
        return BotResponse(text=ch_cfg["disabled_message"])

    session = session_manager.get(chat_id, archive_callback=archive_session)

    # "+N weitere Sorten"-Zeile in der WhatsApp-Liste angetippt → zum Tippen einladen
    # (WhatsApp-Liste zeigt max 10, der Rest kommt per Name).
    if callback_data.startswith("flavmore_"):
        pid = callback_data.split("_", 1)[1]
        product = cache.get_product_by_id(pid)
        variations = cache.get_variations_available(pid)
        if not product or not variations:
            session_manager.save(chat_id, session)
            return BotResponse(text="Sag mir einfach den Namen der Sorte, die du suchst — dann pack ich sie ein! 🙂",
                               answer_callback_text="Sorte tippen")
        pname = get_variant_display_name(product) or product.get("title", "das Produkt")
        examples = ", ".join(get_variant_display_name(v) for v in variations[:3] if get_variant_display_name(v))
        ex = f" (z.B. {examples})" if examples else ""
        resp_text = (f"Bei *{pname}* haben wir **{len(variations)} Sorten** — zu viele für die Liste! 😅\n\n"
                     f"Tippe einfach den Namen deiner Sorte{ex} und die Menge, dann pack ich sie dir ein. 👍")
        # KONTEXT-ANKER in die Gespraechs-Historie: Ohne das ist die KI beim naechsten
        # Text ("Blaubeere") BLIND dafuer, dass es eine Sorte GENAU dieses Produkts sein
        # soll → sie koennte global irgendeine Blaubeere-Variante eines anderen Produkts
        # finden. Wir legen Produkt-Anker + die echte Sorten-Liste als [Bracket]-Marker
        # ab (gleiche Konvention wie "[Button geklickt: ...]" beim Mengen-Klick).
        avail = ", ".join(n for n in (get_variant_display_name(v) for v in variations) if n)
        session.setdefault("conversation", []).append({
            "role": "user",
            "content": (f"[Aktion: 'weitere Sorten' bei {pname} (Artikel {pid}) angetippt. Die NAECHSTE "
                        f"Nachricht des Kunden ist ein Geschmacksname GENAU zu diesem Produkt — ordne ihn "
                        f"ausschliesslich diesem Artikel zu (kein anderes Produkt) und leg die passende "
                        f"Variante in den Warenkorb. Verfuegbare Sorten: {avail[:1500]}]"),
        })
        session["conversation"].append({"role": "assistant", "content": resp_text})
        session_manager.save(chat_id, session)
        return BotResponse(text=resp_text, answer_callback_text="Sorte tippen")

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

    elif callback_data in ("chatorder_rechnung", "chatorder_vorkasse", "chatorder_link"):
        verified = session.get("verified") or {}
        if not session.get("cart"):
            session_manager.save(chat_id, session)
            return BotResponse(text="Dein Warenkorb ist leer — pack erst was rein! 🛒",
                               answer_callback_text="Warenkorb leer")

        if callback_data == "chatorder_link":
            # Lieber im Browser: Magic-Link wie gehabt
            url = None
            if verified.get("email"):
                from dp_connect_bot.services.woocommerce import request_checkout_token
                url = request_checkout_token(verified["email"], session["cart"])
            if not url:
                url = generate_checkout_url(session["cart"])
            session_manager.save(chat_id, session)
            return BotResponse(
                text=("Alles klar! ✨ Dein persönlicher Bestell-Link "
                      "(loggt dich automatisch ein):\n" + url + "\n_Gültig für 15 Minuten._")
                if url else "Hmm, der Link klappt gerade nicht — versuch's gleich nochmal!",
                answer_callback_text="Link kommt!",
            )

        # Direktbestellung im Chat
        if not verified.get("customer_id"):
            session_manager.save(chat_id, session)
            return BotResponse(text="Dafür musst du verifiziert sein — schick mir kurz deine E-Mail-Adresse!",
                               answer_callback_text="Nicht verifiziert")
        pending = session.get("pending_chat_order") or {}
        method = "rechnung" if callback_data == "chatorder_rechnung" else "vorkasse"
        if method == "rechnung" and not pending.get("rechnung_erlaubt"):
            session_manager.save(chat_id, session)
            return BotResponse(text="Kauf auf Rechnung ist für dein Konto noch nicht freigeschaltet — nimm Vorkasse oder frag bei Davides Team an. 🙏",
                               answer_callback_text="Nicht freigeschaltet")

        # Doppelklick-/Doppelbestellungs-Schutz: ein zweiter Klick waehrend die
        # Anlage laeuft wird abgefangen (sonst zwei echte WC-Bestellungen).
        if session.get("chat_order_inflight"):
            session_manager.save(chat_id, session)
            return BotResponse(text="Moment, deine Bestellung wird gerade angelegt — bitte nicht doppelt klicken! ⏳",
                               answer_callback_text="Wird schon bearbeitet")
        session["chat_order_inflight"] = True
        session_manager.save(chat_id, session)

        from dp_connect_bot.services.chat_order import create_order
        # create_order baut die Items VOR seinem try auf — wirft dort etwas (z.B.
        # int() auf eine kaputte Menge), bliebe inflight sonst dauerhaft True und
        # der Kunde koennte NIE wieder bestellen ("wird gerade angelegt"). Darum
        # hart umschliessen: inflight wird IMMER zurueckgesetzt.
        try:
            res = create_order(verified["customer_id"], session["cart"], method, channel)
        except Exception as e:
            log.error(f"[chatorder] create_order raised: {e}", exc_info=True)
            res = {"ok": False, "error": True}
        session["chat_order_inflight"] = False
        if res.get("ok"):
            session["last_order"] = [dict(i) for i in session["cart"]]
            session["cart"] = []
            session["pending_chat_order"] = None
            session["status"] = "ordered"
            session_manager.save(chat_id, session)
            track_event("chat_order_created", chat_id, channel, f"#{res.get('number')} {method}")
            total = res.get("total", "")
            if method == "rechnung":
                pay_info = "Die Ware geht raus — die Rechnung kommt wie gewohnt per E-Mail. 🚚"
            else:
                pay_info = "Du bekommst gleich die Bestellbestätigung mit den Überweisungsdaten per E-Mail. Nach Zahlungseingang geht die Ware raus! 🚚"
            return BotResponse(
                text=(f"🎉 *Bestellung #{res.get('number')} ist drin!*\n\n"
                      f"💰 {str(total).replace('.', ',')}€ ({res.get('payment')})\n"
                      f"{pay_info}\n\n"
                      "Danke für deine Bestellung! 🙌"),
                answer_callback_text="Bestellt! 🎉",
                wc_actions=[WcAction(action="clear")],
            )
        if res.get("forbidden"):
            session_manager.save(chat_id, session)
            return BotResponse(text="Kauf auf Rechnung ist für dein Konto nicht freigeschaltet — nimm Vorkasse. 🙏",
                               answer_callback_text="Nicht freigeschaltet")
        session_manager.save(chat_id, session)
        return BotResponse(
            text="Da hat was geklemmt — die Bestellung wurde NICHT angelegt. Versuch's nochmal oder nutze den Browser-Checkout. 🙏",
            answer_callback_text="Fehler",
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

    # Unbekannter/veralteter Button — z.B. aus einer aelteren WhatsApp-Nachricht, deren
    # Flow es nach einem Deploy nicht mehr gibt. NICHT stumm schlucken: der Kunde hat
    # bewusst etwas angetippt und erwartet eine Reaktion; Stille wirkt wie ein Bug.
    log.info(f"[{channel}:{chat_id}] Unbekanntes Callback ignoriert: {callback_data!r}")
    return BotResponse(
        text="Hoppla, dieser Knopf ist gerade nicht mehr aktiv. 🙂 Sag mir einfach, was du brauchst — z.B. eine Marke oder Sorte, dann geht's weiter.",
        answer_callback_text="Nicht mehr aktiv",
    )


def _handle_flavor_selection(session, chat_id, callback_data):
    """Handle flavor/variant button click."""
    product_id = callback_data[4:]
    product = cache.get_product_by_id(product_id)

    if not product:
        # Veralteter Sorten-Button (Produkt seit Anzeige entfernt). answer_callback_text
        # ist NUR Telegram → auf WhatsApp saehe der Kunde sonst NICHTS. Sichtbarer Text.
        return BotResponse(
            text="Diese Sorte ist gerade nicht mehr verfügbar. 😕 Sag mir einfach, welche andere Sorte oder Marke du möchtest!",
            answer_callback_text="Nicht mehr verfügbar",
        )

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
    if len(parts) != 3 or not parts[2].isdigit():
        return BotResponse(is_silent=True)

    product_id = parts[1]
    quantity = int(parts[2])

    product = cache.get_product_by_id(product_id)
    if not product:
        # Veralteter Mengen-Button (Produkt weg) → auf WhatsApp sonst stumm.
        return BotResponse(
            text="Das Produkt ist gerade nicht mehr verfügbar. 😕 Sag mir einfach, was du stattdessen brauchst!",
            answer_callback_text="Nicht mehr verfügbar",
        )
    if product.get("produkt_typ") == "variable":
        # Mengen-Button auf einem variablen Eltern-Produkt (sollte nicht passieren,
        # aber defense-in-depth) → es braucht eine Sorte, nicht den Parent.
        return BotResponse(
            text=f"Von *{get_variant_display_name(product)}* brauche ich noch die genaue Sorte. 👇",
            keyboards=[Keyboard(type=KeyboardType.FLAVORS, parent_id=product_id)],
            answer_callback_text="Sorte wählen",
        )

    name = get_variant_display_name(product)
    price = product.get("price", "")
    brand = product.get("brand", "")

    # Add to cart
    from dp_connect_bot.services.cart_processing import _apply_staffel_price
    session.setdefault("cart", [])  # defensiv: alte Sessions ohne cart-Key
    existing = next((i for i in session["cart"] if str(i["product_id"]) == product_id), None)
    if existing:
        existing["quantity"] += quantity
        _apply_staffel_price(existing, product)  # Mengen-Button kann Staffel-Schwelle kreuzen
        total_qty = existing["quantity"]
    else:
        img_url = product.get("image_url", "")
        if not img_url and product.get("post_parent"):
            parent_p = cache.get_product_by_id(product["post_parent"])
            if parent_p:
                img_url = parent_p.get("image_url", "")
        new_item = {
            "product_id": product_id,
            "title": f"{brand} - {name}".strip(" -"),
            "quantity": quantity,
            "price": str(price),
            "image_url": img_url,
        }
        _apply_staffel_price(new_item, product)  # Staffelpreis fuer die gewaehlte Menge
        session["cart"].append(new_item)
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
        # Veralteter Button (Produkt weg) → auf WhatsApp sonst stumm.
        return BotResponse(
            text="Das Produkt ist gerade nicht mehr verfügbar. 😕 Was darf ich dir stattdessen einpacken?",
            answer_callback_text="Nicht mehr verfügbar",
        )

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
    # Bevorzugt die bereits aufgefrischte Liste aus handle_reorder; sonst
    # last_order frisch validieren (tote Produkte raus, Live-Preise).
    from dp_connect_bot.handlers.cart import refresh_reorder_items
    pending = session.pop("reorder_pending", None)
    if pending:
        last_order = pending
    else:
        last_order, _dropped = refresh_reorder_items(session.get("last_order", []))
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


def _recent_customer_issue(session, max_msgs=3, max_len=500):
    """Fasst die letzten ECHTEN Kundennachrichten (keine [Button]-Marker, keine
    /Commands) als Anliegen-Beschreibung für die Eskalations-Benachrichtigung zusammen."""
    out = []
    for m in reversed(session.get("conversation", [])):
        if m.get("role") != "user":
            continue
        c = str(m.get("content", "")).strip()
        if not c or c.startswith("[") or c.startswith("/"):
            continue
        out.append(c)
        if len(out) >= max_msgs:
            break
    return " | ".join(reversed(out))[:max_len]


def _handle_callback_request(session, chat_id, callback_data):
    """Kontakt-Button (Rückruf/E-Mail/WhatsApp) nach [REQUEST_CALLBACK].

    Diese Buttons erscheinen NUR, nachdem die KI einen Hilfe-Wunsch erkannt hat —
    der Kunde hat sein Anliegen also bereits geschildert. Darum NICHT erneut nach
    der Beschreibung fragen (das war eine frustrierende Schleife), sondern direkt an
    Davides Team übergeben + benachrichtigen (Pushover-Push + tools.dpconnect.de)."""
    contact_type = callback_data[3:]  # email, phone, whatsapp, call
    channel = session.get("channel", "telegram")
    pref = {"call": "Rückruf", "phone": "Rückruf",
            "email": "E-Mail", "whatsapp": "WhatsApp"}.get(contact_type, contact_type)

    # Ab jetzt übernimmt das Team — Bot antwortet nicht mehr automatisch, Folge-
    # nachrichten werden weitergeleitet. Ausstieg jederzeit über das Menü/​/start.
    session["human_mode"] = True
    session["mode"] = "support"
    session["support_step"] = None
    session["conversation"].append(
        {"role": "user", "content": f"[Kontakt gewählt: {pref} — Kunde wartet auf Davides Team]"})
    track_event("callback_requested", chat_id, channel, contact_type)

    issue = _recent_customer_issue(session)
    customer_name = session.get("customer_name") or (session.get("verified") or {}).get("name", "")
    # Team benachrichtigen — beide Wege defensiv, ein Fehler darf den Chat nie stören.
    try:
        from dp_connect_bot.services.pushover import notify_escalation
        notify_escalation(chat_id, channel, reason=f"Kunde möchte {pref}",
                          collected_info=issue, customer_name=customer_name)
    except Exception as e:
        log.error(f"[callback] Push fehlgeschlagen: {e}")
    try:
        from dp_connect_bot.services.tools_notify import notify_help_needed
        notify_help_needed(chat_id=chat_id, channel=channel, contact_pref=pref,
                           issue=issue, customer=session.get("verified") or {},
                           customer_name=customer_name)
    except Exception as e:
        log.error(f"[callback] tools-Benachrichtigung fehlgeschlagen: {e}")

    session_manager.save(chat_id, session)

    if contact_type == "whatsapp":
        return BotResponse(
            text=(
                "💬 Alles klar — ich hab dein Anliegen an Davides Team weitergegeben! "
                "Du kannst hier direkt weiterschreiben:\n\n"
                "👉 https://wa.me/4915906192252\n\n"
                "Sie melden sich schnellstmöglich. 👋"
            ),
            answer_callback_text="✅ Weitergeleitet!",
        )

    contact_word = "telefonisch zurück" if contact_type in ("call", "phone") else "per E-Mail"
    return BotResponse(
        text=(
            f"✅ Erledigt — ich hab dein Anliegen direkt an Davides Team weitergegeben. "
            f"Sie melden sich schnellstmöglich {contact_word} bei dir! 🙌\n\n"
            "Wenn du in der Zwischenzeit selbst weitermachen willst, tippe unten."
        ),
        keyboards=[Keyboard(type=KeyboardType.MODE_CHOICE)],
        answer_callback_text="✅ Weitergeleitet!",
    )


# ============================================================
# LOGIN FLOW HELPERS
# ============================================================

def _handle_login_magic(session, chat_id):
    """Handle Login-Link button click."""
    session["mode"] = None
    session["login_step"] = None
    session_manager.save(chat_id, session)

    return BotResponse(
        text=(
            "🔑 *Login-Link*\n\n"
            "Klicke auf diesen Link und gib deine E-Mail-Adresse ein:\n"
            "👉 https://dpconnect.de/anmelden/?action=magic_login\n\n"
            "Du bekommst dann einen Einmal-Link per E-Mail, mit dem du dich "
            "direkt einloggen kannst – ganz ohne Passwort! 🪄\n\n"
            "Danach kannst du in deinem Konto ein neues Passwort setzen."
        ),
        keyboards=[Keyboard(type=KeyboardType.MODE_CHOICE)],
        answer_callback_text="🔑 Login-Link!",
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
                "Versuch es alternativ mit dem Login-Link:\n"
                "👉 https://dpconnect.de/anmelden/?action=magic_login\n\n"
                "Oder kontaktiere uns: +49 221 650 878 78"
            ),
            keyboards=[Keyboard(type=KeyboardType.MODE_CHOICE)],
            answer_callback_text="❌ Fehler",
        )
