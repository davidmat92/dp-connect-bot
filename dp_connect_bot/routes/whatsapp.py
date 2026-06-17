"""WhatsApp webhook route blueprint."""

import threading

import requests
from flask import Blueprint, request, jsonify

from dp_connect_bot.handlers.unified import unified_handle_message, unified_handle_callback
from dp_connect_bot.adapters.whatsapp import WhatsAppAdapter
from dp_connect_bot.services.voice import transcribe_whatsapp_voice
from dp_connect_bot.config import WHATSAPP_VERIFY_TOKEN, log

whatsapp_bp = Blueprint("whatsapp", __name__)
adapter = WhatsAppAdapter()


def _forward_to_dptools(payload):
    """Leitet den rohen Meta-Webhook zusaetzlich an dp-tools weiter (Newsletter-
    Opt-out: 'Nein'-Antworten auf WhatsApp-Vorlagen). Fire-and-forget — die
    Weiterleitung darf den Bot NIEMALS stoeren oder verlangsamen."""
    try:
        requests.post(
            "https://api.tools.dpconnect.de/api/whatsapp/webhook",
            json=payload, timeout=4,
        )
    except Exception:
        pass


_NL_OPTIN_MSG = (
    "Super, du bist dabei! 🎉 Du bekommst ab jetzt unsere Infos zu Angeboten & "
    "Neuheiten. Und klar — du kannst mir hier jederzeit ganz normal schreiben, "
    "wenn du etwas bestellen oder fragen willst. 🛒"
)
_NL_OPTOUT_MSG = (
    "Alles klar, du bekommst von uns keine Newsletter mehr. 👍 Wenn du es dir "
    "anders überlegst, schreib einfach *Newsletter* — dann bist du wieder dabei.\n\n"
    "Und du kannst mich hier natürlich trotzdem jederzeit normal nutzen: sag mir "
    "einfach, was du brauchst! 🛒"
)


def _handle_template_button_replies(payload):
    """Antwortet auf Newsletter-Template-Quick-Replies ('Ja'/'Nein', die als
    Nachrichtentyp 'button' kommen — im Gegensatz zu den EIGENEN Bot-Buttons, die
    als 'interactive'/button_reply kommen). Schickt eine kurze Bestaetigung und
    laesst die normale Chat-/Bestell-Logik aus.

    Returns True, wenn mindestens eine solche Template-Antwort dabei war (dann
    ignoriert der Webhook den Rest und kehrt mit 200 zurueck).
    """
    from dp_connect_bot.services.wa_dedup import seen_before
    found = False
    try:
        for e in payload.get("entry", []):
            for c in e.get("changes", []):
                for m in c.get("value", {}).get("messages", []):
                    if m.get("type") != "button":
                        continue
                    found = True
                    phone = m.get("from")
                    if not phone:
                        continue
                    # Meta-Retry derselben Antwort nicht doppelt bestaetigen
                    if seen_before(m.get("id")):
                        continue
                    btn = m.get("button") or {}
                    label = str(btn.get("payload") or btn.get("text") or "").strip().lower()
                    if "nein" in label or "abmeld" in label or label in ("no",):
                        adapter._send_message(phone, _NL_OPTOUT_MSG)
                    elif "ja" in label or "anmeld" in label or label in ("yes",):
                        adapter._send_message(phone, _NL_OPTIN_MSG)
                    # unbekannte Buttons: still bleiben (nur weitergeleitet)
    except Exception as ex:
        log.error(f"Template-Button-Handling fehlgeschlagen: {ex}")
    return found


@whatsapp_bp.route("/whatsapp", methods=["GET"])
def whatsapp_verify():
    """WhatsApp webhook verification (hub challenge)."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        log.info("WhatsApp webhook verified")
        return challenge, 200
    return "Forbidden", 403


@whatsapp_bp.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    """Receive incoming WhatsApp messages and callbacks."""
    try:
        payload = request.get_json()
        if not payload:
            return jsonify(ok=True), 200

        # Eingehenden Webhook UNVERAENDERT zusaetzlich an dp-tools weiterleiten
        # (Newsletter-Opt-out). Fire-and-forget im Thread: weder langsamer noch
        # blockierend; die Antwort an Meta (HTTP 200) bleibt unveraendert.
        threading.Thread(target=_forward_to_dptools, args=(payload,), daemon=True).start()

        # Newsletter-Template-Antworten ("Ja"/"Nein", type=="button") bekommen eine
        # kurze Bestaetigung und KEINE normale Chat-/Bestell-Logik (sonst Endlos-
        # "tippt..." + Meta-Retries). dp-tools hat den Webhook oben schon erhalten.
        # Eigene interaktive Bot-Buttons (type=="interactive") sind NICHT betroffen.
        if _handle_template_button_replies(payload):
            return jsonify(ok=True), 200

        # Gepufferte Sends nachliefern (nach Meta-Stoerung)
        from dp_connect_bot.services.send_queue import flush, pending_count
        if pending_count():
            flush()

        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                contacts = value.get("contacts", [])

                for msg in messages:
                    phone = msg.get("from")
                    if not phone:
                        continue

                    # Meta wiederholt den Webhook bei langsamer Antwort → dieselbe
                    # Nachricht nicht doppelt verarbeiten (sonst doppelte Adds/Order).
                    from dp_connect_bot.services.wa_dedup import seen_before
                    if seen_before(msg.get("id")):
                        log.info(f"[WA:{phone}] Duplikat-Webhook uebersprungen (msg {msg.get('id')})")
                        continue

                    # Blaue Haken + "tippt..." sofort anzeigen
                    adapter.mark_read_typing(msg.get("id"))

                    name = ""
                    if contacts:
                        name = (contacts[0].get("profile") or {}).get("name", "")
                    user_info = {"first_name": name}

                    # --- Interactive button/list reply (check BEFORE text guard) ---
                    if msg.get("type") == "interactive":
                        interactive = msg.get("interactive", {})
                        btn = interactive.get("button_reply") or interactive.get("list_reply")
                        if btn:
                            text = btn.get("id", "")
                        else:
                            continue

                    elif msg.get("type") == "text":
                        text = (msg.get("text") or {}).get("body", "")
                        if not text:
                            continue

                    # --- Voice message ---
                    elif msg.get("type") == "audio":
                        log.info(f"[WA:{phone}] Voice message received")
                        from dp_connect_bot.services.bot_config import channel_flag
                        if not channel_flag("whatsapp", "voice_enabled"):
                            adapter._send_message(
                                phone,
                                "Sprachnachrichten sind hier gerade deaktiviert. \U0001f64f\n"
                                "Schreib mir einfach, was du brauchst!",
                            )
                            continue
                        adapter._send_message(phone, "\U0001f3a4 _Sprachnachricht wird verarbeitet..._")
                        audio_id = msg.get("audio", {}).get("id")
                        text = transcribe_whatsapp_voice(audio_id)
                        if text:
                            adapter._send_message(phone, f"\U0001f3a4 _{text}_")
                        else:
                            adapter._send_message(
                                phone,
                                "Sorry, ich konnte die Sprachnachricht nicht verstehen. \U0001f605\n"
                                "Kannst du mir stattdessen schreiben was du brauchst?",
                            )
                            continue

                    # --- Foto ("habt ihr das hier?") ---
                    elif msg.get("type") == "image":
                        log.info(f"[WA:{phone}] Foto empfangen")
                        adapter.mark_read_typing(msg.get("id"))
                        media_id = msg.get("image", {}).get("id")
                        caption = msg.get("image", {}).get("caption", "")
                        image_bytes, mime = _download_whatsapp_media(media_id)
                        if not image_bytes:
                            adapter._send_message(phone, "Das Foto konnte ich gerade nicht laden. 😅 Probier's nochmal oder beschreib mir das Produkt!")
                            continue
                        from dp_connect_bot.services.photo_vision import describe_photo, build_photo_message
                        desc = describe_photo(image_bytes, mime)
                        if not desc:
                            adapter._send_message(phone, "Das Foto konnte ich nicht auswerten. 😅 Beschreib mir das Produkt einfach kurz!")
                            continue
                        text = build_photo_message(desc, caption)

                    else:
                        continue  # skip stickers, etc.

                    log.info(f"[WA:{phone}] Message: {text}")
                    prefixed = adapter.prefixed_chat_id(phone)
                    response = unified_handle_message(prefixed, text, user_info, channel="whatsapp")
                    adapter.send_response(phone, response)

        return jsonify(ok=True), 200
    except Exception as e:
        log.error(f"WhatsApp webhook error: {e}", exc_info=True)
        return jsonify(ok=True), 200


def _download_whatsapp_media(media_id):
    """Laedt ein WhatsApp-Medium herunter. Gibt (bytes, mime) oder (None, None)."""
    from dp_connect_bot.config import WHATSAPP_API, WHATSAPP_TOKEN
    import requests as _requests
    if not media_id or not WHATSAPP_TOKEN:
        return None, None
    try:
        meta = _requests.get(
            f"{WHATSAPP_API}/{media_id}",
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
            timeout=10,
        )
        meta.raise_for_status()
        info = meta.json()
        url = info.get("url")
        mime = info.get("mime_type", "image/jpeg")
        if not url:
            return None, None
        data = _requests.get(url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}, timeout=30)
        data.raise_for_status()
        return data.content, mime
    except Exception as e:
        log.error(f"WhatsApp-Media-Download fehlgeschlagen: {e}")
        return None, None
