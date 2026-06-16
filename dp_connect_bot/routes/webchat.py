import hashlib
import hmac
import time

from flask import Blueprint, request, jsonify

from dp_connect_bot.config import BETA_HINT_PLAIN, WP_BOT_SECRET, log
from dp_connect_bot.handlers.unified import unified_handle_message, unified_handle_callback
from dp_connect_bot.adapters.webchat import WebchatAdapter
from dp_connect_bot.models.session import session_manager

webchat_bp = Blueprint("webchat", __name__)


def _validate_webchat_auth(uid, email, auth) -> bool:
    """Prueft das HMAC-signierte Identitaets-Token aus dem WP-Widget.

    Das Widget signiert serverseitig mit dem geteilten Secret (WP: DP_BOT_SECRET
    == Bot: WP_BOT_SECRET): auth = '<ts>.<hexsig>' ueber 'uid|email|ts'. Ohne
    gueltige Signatur darf ein Client KEINEN verifizierten B2B-Status bekommen
    (sonst koennte jeder per beliebiger wp_user_id Preise/fremde Bestellungen abgreifen).
    """
    if not WP_BOT_SECRET or not auth or "." not in str(auth):
        return False
    try:
        ts_str, sig = str(auth).split(".", 1)
        ts = int(ts_str)
    except (ValueError, TypeError):
        return False
    if abs(time.time() - ts) > 86400:  # Token max. 24h gueltig
        return False
    payload = f"{uid}|{email}|{ts}"
    expected = hmac.new(WP_BOT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


_init_hits = {}  # {ip: [timestamps]} — Flood-Schutz (pro Worker, reicht als Bremse)


def _init_flood(ip: str, limit: int = 50, window: int = 3600) -> bool:
    import time as _time
    now = _time.time()
    hits = [t for t in _init_hits.get(ip, []) if now - t < window]
    hits.append(now)
    _init_hits[ip] = hits
    if len(_init_hits) > 5000:  # Speicher-Bremse
        _init_hits.clear()
    return len(hits) > limit


@webchat_bp.route("/chat/init", methods=["POST", "OPTIONS"])
def webchat_init():
    if request.method == "OPTIONS":
        return "", 204
    try:
        ip = request.headers.get("X-Real-IP", request.remote_addr or "?")
        if _init_flood(ip):
            log.warning(f"[webchat_init] Flood von {ip}")
            return jsonify(ok=False, error="rate_limited"), 429
        data = request.get_json() or {}
        visitor_id = data.get("visitor_id", str(time.time()))
        chat_id = f"web_{hashlib.md5(visitor_id.encode()).hexdigest()[:12]}"

        session = session_manager.get(chat_id)
        session["channel"] = "web"

        if data.get("customer_name"):
            session["customer_name"] = data["customer_name"]

        wp_user_id = data.get("wp_user_id")
        # SICHERHEIT: clientseitige wp_user_id darf NUR mit gueltiger HMAC-Signatur
        # des WP-Widgets verifizierten B2B-Status geben. Stufenweise Einfuehrung
        # ueber die Config-Flagge (erst loggen, dann scharfschalten).
        from dp_connect_bot.services.bot_config import load_bot_config
        auth_ok = _validate_webchat_auth(wp_user_id, data.get("wp_email", ""), data.get("wp_auth", "")) if wp_user_id else False
        enforce = bool(load_bot_config().get("webchat_require_signed_auth"))

        if wp_user_id:
            # Audit fuer den Staging-Rollout: zeigt im Admin, ob eingeloggte
            # Kunden korrekt signieren (bevor scharfgeschaltet wird).
            from dp_connect_bot.services.history import track_event
            track_event("webchat_auth", chat_id, "web",
                        f"uid={wp_user_id} valid={int(bool(auth_ok))} enforce={int(enforce)}")

        if wp_user_id and not (enforce and not auth_ok):
            if auth_ok:
                log.info(f"[webchat_init] Signatur gueltig fuer wp_user_id={wp_user_id}")
            else:
                log.warning(f"[webchat_init] wp_user_id={wp_user_id} OHNE gueltige Signatur "
                            f"(enforce={'AN' if enforce else 'AUS'}, {'verworfen' if enforce else 'noch akzeptiert'})")
            session["is_guest"] = False
            session.setdefault("user_info", {}).update({
                "wp_user_id": wp_user_id,
                "wp_display_name": data.get("wp_display_name", ""),
                "wp_email": data.get("wp_email", ""),
                "wp_username": data.get("wp_username", ""),
            })
            if not session.get("customer_name") and data.get("wp_display_name"):
                session["customer_name"] = data["wp_display_name"]
            # Eingeloggte Shop-Kunden sind automatisch verifiziert (B2B-Preise)
            session["verified"] = {
                "customer_id": wp_user_id,
                "name": data.get("wp_display_name", ""),
                "firma": "",
                "email": data.get("wp_email", ""),
            }
        else:
            if wp_user_id:
                log.warning(f"[webchat_init] wp_user_id={wp_user_id} OHNE gueltige Signatur → Gast (enforce AN)")
            session["is_guest"] = True

        session_manager.save(chat_id, session)

        name = session.get("customer_name", "")
        welcome = (
            f"Hey{' ' + name if name else ''}! 👋\n\n"
            f"Ich bin dein DP Connect Bestell-Assistent.\n\n"
            f"Sag mir einfach was du brauchst – ich find's für dich!{BETA_HINT_PLAIN}"
        )
        return jsonify(ok=True, chat_id=chat_id, message=welcome)
    except Exception as e:
        log.error(f"[webchat_init] Error: {e}")
        return jsonify(ok=False, error="Internal error"), 500


@webchat_bp.route("/chat/send", methods=["POST", "OPTIONS"])
def webchat_send():
    if request.method == "OPTIONS":
        return "", 204
    try:
        data = request.get_json()
        if not data or not data.get("chat_id") or not data.get("message"):
            return jsonify(ok=False, error="Missing chat_id or message"), 400

        chat_id = data["chat_id"]
        if not chat_id.startswith("web_"):
            return jsonify(ok=False, error="Invalid chat_id"), 400

        text = data["message"]
        wc_cart = data.get("wc_cart")

        log.info(f"[WEB:{chat_id}] {text}")

        response = unified_handle_message(chat_id, text, channel="web", wc_cart=wc_cart)
        adapter = WebchatAdapter()
        result = adapter.build_json_response(response)
        return jsonify(ok=True, **result)
    except Exception as e:
        log.error(f"[webchat_send] Error: {e}")
        return jsonify(ok=False, error="Internal error"), 500


@webchat_bp.route("/chat/action", methods=["POST", "OPTIONS"])
def webchat_action():
    if request.method == "OPTIONS":
        return "", 204
    try:
        data = request.get_json()
        chat_id = data.get("chat_id")
        callback = data.get("callback", "")

        if not chat_id or not chat_id.startswith("web_"):
            return jsonify(ok=False, error="Invalid chat_id"), 400

        log.info(f"[WEB:{chat_id}] Action: {callback}")

        response = unified_handle_callback(chat_id, callback, channel="web")
        adapter = WebchatAdapter()
        result = adapter.build_json_response(response)
        return jsonify(ok=True, **result)
    except Exception as e:
        log.error(f"[webchat_action] Error: {e}")
        return jsonify(ok=False, error="Internal error"), 500


@webchat_bp.route("/chat/has_last_order", methods=["GET", "OPTIONS"])
def chat_has_last_order():
    if request.method == "OPTIONS":
        return "", 204
    try:
        chat_id = request.args.get("chat_id", "")
        if not chat_id:
            return jsonify(ok=False)

        session = session_manager.get(chat_id)
        last_order = session.get("last_order")

        if not last_order:
            return jsonify(ok=True, has_last_order=False, items=[])

        return jsonify(ok=True, has_last_order=True, items=[
            {"title": i.get("title", ""), "quantity": i.get("quantity", 0)}
            for i in last_order
        ])
    except Exception as e:
        log.error(f"[chat_has_last_order] Error: {e}")
        return jsonify(ok=False, error="Internal error"), 500
