"""Telegram webhook route blueprint."""

from flask import Blueprint, request, jsonify

from dp_connect_bot.handlers.unified import unified_handle_message, unified_handle_callback
from dp_connect_bot.adapters.telegram import TelegramAdapter
from dp_connect_bot.services.voice import transcribe_telegram_voice
from dp_connect_bot.config import log

telegram_bp = Blueprint("telegram", __name__)
adapter = TelegramAdapter()


@telegram_bp.route("/webhook", methods=["POST"])
def telegram_webhook():
    try:
        update = request.get_json()
        if not update:
            return jsonify(ok=True), 200

        # --- Normal text message ---
        message = update.get("message")
        if message and message.get("text"):
            chat_id = message["chat"]["id"]
            text = message["text"]
            user_info = message.get("from", {})
            log.info(f"[TG:{chat_id}] {user_info.get('first_name', '?')}: {text}")

            adapter.send_typing(chat_id)
            prefixed = adapter.prefixed_chat_id(chat_id)
            response = unified_handle_message(prefixed, text, user_info, channel="telegram")
            adapter.send_response(chat_id, response)

        # --- Foto ("habt ihr das hier?") ---
        elif message and message.get("photo"):
            chat_id = message["chat"]["id"]
            user_info = message.get("from", {})
            caption = message.get("caption", "")
            # Groesste Variante nehmen (Telegram liefert mehrere Aufloesungen)
            file_id = message["photo"][-1].get("file_id")
            log.info(f"[TG:{chat_id}] Foto empfangen")
            adapter.send_typing(chat_id)

            image_bytes = _download_telegram_file(file_id)
            if not image_bytes:
                adapter._send_message(chat_id, "Das Foto konnte ich gerade nicht laden. 😅 Probier's nochmal oder beschreib mir das Produkt!")
                return jsonify(ok=True), 200
            from dp_connect_bot.services.photo_vision import describe_photo, build_photo_message
            desc = describe_photo(image_bytes, caption=caption)
            if not desc:
                adapter._send_message(chat_id, "Das Foto konnte ich nicht auswerten. 😅 Beschreib mir das Produkt einfach kurz!")
                return jsonify(ok=True), 200
            text = build_photo_message(desc, caption)
            prefixed = adapter.prefixed_chat_id(chat_id)
            response = unified_handle_message(prefixed, text, user_info, channel="telegram")
            adapter.send_response(chat_id, response)

        # --- Geteilter Kontakt (Nummer-teilen-Button → B2B-Verifizierung) ---
        elif message and message.get("contact"):
            chat_id = message["chat"]["id"]
            contact = message["contact"]
            from_id = (message.get("from") or {}).get("id")
            # Nur die EIGENE Nummer akzeptieren (nicht weitergeleitete Kontakte)
            if contact.get("user_id") != from_id:
                adapter._send_message(chat_id, "Bitte teile deine eigene Nummer über den Button. 🙂")
                return jsonify(ok=True), 200

            from dp_connect_bot.services import verification as verif
            from dp_connect_bot.models.session import session_manager
            prefixed = adapter.prefixed_chat_id(chat_id)
            session = session_manager.get(prefixed)
            result = verif.lookup_phone(contact.get("phone_number", ""))
            if result.get("found"):
                verif.mark_verified(session, result["customer"], chat_id=prefixed)
                session["mode"] = "order"
                session_manager.save(prefixed, session)
                name = result["customer"].get("name", "")
                adapter._send_message(
                    chat_id,
                    f"✅ Verifiziert{', ' + name if name else ''}! 🎉\n\n"
                    "Ab jetzt siehst du alle Preise und kannst direkt bestellen. Was brauchst du? 🛒",
                )
            else:
                session_manager.save(prefixed, session)
                adapter._send_message(
                    chat_id,
                    "Zu dieser Nummer finde ich leider kein Kundenkonto. 🤔\n\n"
                    "Bist du mit einer anderen Nummer registriert? Dann schick mir deine "
                    "*E-Mail-Adresse* — ich schicke dir einen Verifizierungscode!\n\n"
                    "Noch kein Kunde? Kostenlos registrieren: https://dpconnect.de/kunde-werden/",
                )

        # --- Voice / audio message ---
        elif message and (message.get("voice") or message.get("audio")):
            chat_id = message["chat"]["id"]
            user_info = message.get("from", {})
            voice = message.get("voice") or message.get("audio")
            file_id = voice.get("file_id")
            log.info(f"[TG:{chat_id}] Voice message received")

            from dp_connect_bot.services.bot_config import channel_flag
            if not channel_flag("telegram", "voice_enabled"):
                adapter._send_message(
                    chat_id,
                    "Sprachnachrichten sind hier gerade deaktiviert. \U0001f64f\n"
                    "Schreib mir einfach, was du brauchst!",
                )
            else:
                adapter._send_message(chat_id, "\U0001f3a4 _Sprachnachricht wird verarbeitet..._")
                text = transcribe_telegram_voice(file_id)
                if text:
                    adapter._send_message(chat_id, f"\U0001f3a4 _{text}_")
                    prefixed = adapter.prefixed_chat_id(chat_id)
                    response = unified_handle_message(prefixed, text, user_info, channel="telegram")
                    adapter.send_response(chat_id, response)
                else:
                    adapter._send_message(
                        chat_id,
                        "Sorry, ich konnte die Sprachnachricht nicht verstehen. \U0001f605\n"
                        "Kannst du mir stattdessen schreiben was du brauchst?",
                    )

        # --- Callback from inline keyboard ---
        callback = update.get("callback_query")
        if callback:
            callback_id = callback.get("id")
            cb_text = ""
            try:
                chat_id = (callback.get("message") or {}).get("chat", {}).get("id")
                data = callback.get("data", "")
                if chat_id is not None:
                    log.info(f"[TG:{chat_id}] Callback: {data}")
                    prefixed = adapter.prefixed_chat_id(chat_id)
                    response = unified_handle_callback(prefixed, data, channel="telegram")
                    adapter.send_response(chat_id, response)
                    cb_text = response.answer_callback_text or ""
            except Exception as e:
                log.error(f"Telegram callback handling error: {e}", exc_info=True)
            finally:
                # Den Lade-Spinner am Button IMMER stoppen — auch wenn die
                # Verarbeitung oben crasht, sonst dreht er beim Kunden ewig weiter.
                if callback_id:
                    adapter.answer_callback(callback_id, cb_text)

        return jsonify(ok=True), 200
    except Exception as e:
        log.error(f"Telegram webhook error: {e}", exc_info=True)
        return jsonify(ok=True), 200


def _download_telegram_file(file_id):
    """Laedt eine Telegram-Datei herunter. Gibt bytes oder None."""
    from dp_connect_bot.config import TELEGRAM_API, TELEGRAM_TOKEN
    import requests as _requests
    if not file_id or not TELEGRAM_TOKEN:
        return None
    try:
        resp = _requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}, timeout=10)
        resp.raise_for_status()
        file_path = resp.json()["result"]["file_path"]
        data = _requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}", timeout=30)
        data.raise_for_status()
        return data.content
    except Exception as e:
        log.error(f"Telegram-File-Download fehlgeschlagen: {e}")
        return None
