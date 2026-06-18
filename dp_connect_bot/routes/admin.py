import json
import re
import sqlite3
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify

from dp_connect_bot.config import ADMIN_API_KEY, HISTORY_DB_PATH, SESSION_TIMEOUT_HOURS, log
from dp_connect_bot.models.session import session_manager
from dp_connect_bot.adapters.telegram import TelegramAdapter
from dp_connect_bot.adapters.whatsapp import WhatsAppAdapter
from dp_connect_bot.services.bot_config import (
    load_bot_config, save_bot_config, get_channel_config,
    CHANNELS, CHANNEL_BOOL_FLAGS,
)

admin_bp = Blueprint("admin", __name__)


def _require_admin():
    key = request.headers.get("X-Admin-Key", "")
    return key == ADMIN_API_KEY


@admin_bp.route("/admin/reload-cache", methods=["POST", "GET"])
def admin_reload_cache():
    """Laedt den Produkt-Cache SYNCHRON aus WooCommerce neu (frische Preise/Lager)
    und speichert den Snapshot. Gedacht fuer einen PythonAnywhere-Scheduled-Task
    (Cron alle ~15 Min) — der Hintergrund-Thread-Refresh laeuft auf PA nicht
    zuverlaessig. Andere Worker uebernehmen den frischen Snapshot automatisch."""
    if not _require_admin():
        return jsonify(ok=False, error="Unauthorized"), 401
    import time as _t
    from dp_connect_bot.services.product_cache import cache
    t0 = _t.monotonic()
    try:
        cache.load()
    except Exception as e:
        log.error(f"reload-cache fehlgeschlagen: {e}")
        return jsonify(ok=False, error=str(e)[:300]), 200
    return jsonify(
        ok=True,
        source=cache.source,
        available=len(cache.available),
        total=len(cache.all_products),
        took_s=round(_t.monotonic() - t0, 1),
    ), 200


@admin_bp.route("/admin/reorder-reminders", methods=["POST", "GET"])
def admin_reorder_reminders():
    """Proaktive Nachbestell-Erinnerungen anstossen (fuer einen taeglichen
    Scheduled-Task). `?dry_run=1` (oder JSON {"dry_run": true}) sendet NICHTS und
    gibt nur die faelligen Kandidaten zurueck — zum gefahrlosen Pruefen. Echtes
    Senden nur wenn `reorder_reminders_enabled` in der Bot-Config AN ist."""
    if not _require_admin():
        return jsonify(ok=False, error="Unauthorized"), 401
    data = request.get_json(silent=True) or {}
    # Test-Send: einmaliges Template an EINE Nummer (Beispielwerte) zum Pruefen des
    # Renderings — unabhaengig vom enabled-Flag und der Faelligkeit.
    test_phone = (request.args.get("test_phone") or data.get("test_phone") or "").strip()
    if test_phone:
        try:
            from dp_connect_bot.services.bot_config import load_bot_config
            cfg = load_bot_config()
            tmpl = (cfg.get("reorder_wa_template") or "").strip()
            lang = (cfg.get("reorder_wa_lang") or "de").strip()
            if not tmpl:
                return jsonify(ok=False, error="reorder_wa_template ist nicht gesetzt"), 200
            from dp_connect_bot.adapters.whatsapp import WhatsAppAdapter
            ok = WhatsAppAdapter().send_template(test_phone, tmpl, ["Test", "21"], lang=lang)
            return jsonify(ok=bool(ok), test_send=True, template=tmpl, lang=lang, to=test_phone), 200
        except Exception as e:
            log.error(f"[admin_reorder_reminders] test_send: {e}")
            return jsonify(ok=False, error=str(e)[:200]), 500
    dry = (request.args.get("dry_run") in ("1", "true", "yes")) or bool(data.get("dry_run"))
    try:
        from dp_connect_bot.services.reorder_reminders import check_and_remind
        return jsonify(check_and_remind(dry_run=dry)), 200
    except Exception as e:
        log.error(f"[admin_reorder_reminders] {e}")
        return jsonify(ok=False, error=str(e)[:200]), 500


@admin_bp.route("/admin/sessions", methods=["GET"])
def admin_sessions():
    if not _require_admin():
        return jsonify(ok=False, error="Unauthorized"), 401
    try:
        channel_filter = request.args.get("channel")
        active_only = request.args.get("active", "true") == "true"
        now = datetime.now()
        all_sessions = session_manager.get_all()

        result = []
        for cid, s in all_sessions.items():
            if channel_filter and s.get("channel") != channel_filter:
                continue

            try:
                last = datetime.fromisoformat(s.get("last_activity", "2000-01-01"))
                hours_ago = (now - last).total_seconds() / 3600
            except Exception:
                hours_ago = 999

            if active_only and hours_ago > SESSION_TIMEOUT_HOURS:
                continue

            conv = s.get("conversation", [])
            user_msgs = sum(1 for m in conv if m.get("role") == "user")
            bot_msgs = sum(1 for m in conv if m.get("role") == "assistant")

            last_user_msg = ""
            for m in reversed(conv):
                if m.get("role") == "user":
                    txt = m.get("content", "")
                    if "[KUNDE]" in txt:
                        txt = txt.split("[KUNDE]")[-1].strip()
                    elif "[PRODUKTDATEN]" in txt:
                        txt = txt.split("[KUNDE]")[-1].strip() if "[KUNDE]" in txt else txt
                    last_user_msg = txt[:120]
                    break

            result.append({
                "chat_id": cid,
                "channel": s.get("channel", "?"),
                "customer_name": s.get("customer_name", ""),
                "user_info": s.get("user_info", {}),
                "status": s.get("status", "browsing"),
                "message_count": s.get("message_count", 0),
                "user_msgs": user_msgs,
                "bot_msgs": bot_msgs,
                "cart_items": len(s.get("cart", [])),
                "created_at": s.get("created_at", ""),
                "last_activity": s.get("last_activity", ""),
                "hours_ago": round(hours_ago, 1),
                "last_message": last_user_msg,
                "is_human_mode": s.get("human_mode", False),
            })

        result.sort(key=lambda x: x.get("last_activity", ""), reverse=True)
        return jsonify(ok=True, sessions=result, total=len(result))
    except Exception as e:
        log.error(f"[admin_sessions] Error: {e}")
        return jsonify(ok=False, error="Internal error"), 500


@admin_bp.route("/admin/conversation/<chat_id>", methods=["GET"])
def admin_conversation(chat_id):
    if not _require_admin():
        return jsonify(ok=False, error="Unauthorized"), 401
    try:
        all_sessions = session_manager.get_all()
        if chat_id not in all_sessions:
            return jsonify(ok=False, error="Session not found"), 404

        s = all_sessions[chat_id]
        conv = s.get("conversation", [])
        clean_conv = []
        for m in conv:
            txt = m.get("content", "")
            role = m.get("role", "")
            if role == "user" and "[KUNDE]" in txt:
                txt = txt.split("[KUNDE]")[-1].strip()
            if role == "assistant":
                txt = re.sub(r"\s*```cart_action\n.*?\n```\s*", "", txt, flags=re.DOTALL).strip()
                txt = re.sub(r"\s*\[SHOW_FLAVORS:\d+\]\s*", "", txt).strip()
                txt = re.sub(r"\s*\[SHOW_QUANTITIES:\d+\]\s*", "", txt).strip()
            clean_conv.append({"role": role, "content": txt})

        return jsonify(
            ok=True,
            chat_id=chat_id,
            channel=s.get("channel", "?"),
            customer_name=s.get("customer_name", ""),
            user_info=s.get("user_info", {}),
            status=s.get("status", ""),
            cart=s.get("cart", []),
            message_count=s.get("message_count", 0),
            created_at=s.get("created_at", ""),
            last_activity=s.get("last_activity", ""),
            conversation=clean_conv,
            is_human_mode=s.get("human_mode", False),
        )
    except Exception as e:
        log.error(f"[admin_conversation] Error: {e}")
        return jsonify(ok=False, error="Internal error"), 500


@admin_bp.route("/admin/reply", methods=["POST"])
def admin_reply():
    if not _require_admin():
        return jsonify(ok=False, error="Unauthorized"), 401
    try:
        data = request.get_json()
        chat_id = data.get("chat_id", "")
        message = data.get("message", "").strip()
        enable_human_mode = data.get("human_mode")

        all_sessions = session_manager.get_all()
        if chat_id not in all_sessions:
            return jsonify(ok=False, error="Session not found"), 404

        s = session_manager.get(chat_id)

        if enable_human_mode is not None:
            s["human_mode"] = bool(enable_human_mode)
            session_manager.save(chat_id, s)
            if not message:
                return jsonify(ok=True, human_mode=s["human_mode"])

        if not message:
            return jsonify(ok=False, error="No message"), 400

        channel = s.get("channel", "")
        raw_id = chat_id.split("_", 1)[1] if "_" in chat_id else chat_id

        if channel == "telegram":
            tg = TelegramAdapter()
            sent = tg._send_message(raw_id, f"\U0001f464 {message}")
            if not sent:
                log.error(f"[admin_reply] Telegram send failed for {raw_id}")
                return jsonify(ok=False, error="Telegram-Nachricht konnte nicht gesendet werden"), 502
            s["conversation"].append({"role": "assistant", "content": f"[ADMIN] {message}"})
            session_manager.save(chat_id, s)
            return jsonify(ok=True, sent=True, channel="telegram")

        elif channel == "whatsapp":
            wa = WhatsAppAdapter()
            sent = wa._send_message(raw_id, f"\U0001f464 {message}")
            if not sent:
                log.error(f"[admin_reply] WhatsApp send failed for {raw_id}")
                return jsonify(ok=False, error="WhatsApp-Nachricht konnte nicht gesendet werden"), 502
            s["conversation"].append({"role": "assistant", "content": f"[ADMIN] {message}"})
            session_manager.save(chat_id, s)
            return jsonify(ok=True, sent=True, channel="whatsapp")

        elif channel == "web":
            s["conversation"].append({"role": "assistant", "content": f"[ADMIN] {message}"})
            s.setdefault("pending_admin_messages", []).append(message)
            session_manager.save(chat_id, s)
            return jsonify(ok=True, sent=True, channel="web", note="Message queued for next poll")

        return jsonify(ok=False, error=f"Unknown channel: {channel}"), 400
    except Exception as e:
        log.error(f"[admin_reply] Error: {e}")
        return jsonify(ok=False, error="Internal error"), 500


@admin_bp.route("/admin/stats", methods=["GET"])
def admin_stats():
    if not _require_admin():
        return jsonify(ok=False, error="Unauthorized"), 401
    try:
        days = int(request.args.get("days", 30))
        now = datetime.now()
        all_sessions = session_manager.get_all()

        total_sessions = len(all_sessions)
        active_24h = 0
        active_1h = 0
        channels = {"web": 0, "telegram": 0, "whatsapp": 0}
        total_messages = 0
        total_cart_items = 0
        sessions_with_cart = 0
        product_mentions = {}
        conv_lengths = []

        for cid, s in all_sessions.items():
            try:
                last = datetime.fromisoformat(s.get("last_activity", "2000-01-01"))
                hours = (now - last).total_seconds() / 3600
            except Exception:
                hours = 999

            if hours <= 24:
                active_24h += 1
            if hours <= 1:
                active_1h += 1

            ch = s.get("channel", "?")
            if ch in channels:
                channels[ch] += 1

            mc = s.get("message_count", 0)
            total_messages += mc
            if mc > 0:
                conv_lengths.append(mc)

            cart = s.get("cart", [])
            if cart:
                sessions_with_cart += 1
                total_cart_items += len(cart)
                for item in cart:
                    title = item.get("title", "Unknown")
                    product_mentions[title] = product_mentions.get(title, 0) + item.get("quantity", 1)

        top_products = sorted(product_mentions.items(), key=lambda x: x[1], reverse=True)[:20]
        avg_conv_length = round(sum(conv_lengths) / max(len(conv_lengths), 1), 1)

        # Historical stats from SQLite
        daily_data = []
        top_searches = []
        search_no_results = []
        drop_off_rate = 0
        total_archived = 0

        try:
            with sqlite3.connect(HISTORY_DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")

                rows = conn.execute(
                    "SELECT * FROM daily_stats WHERE date >= ? ORDER BY date ASC",
                    (cutoff,),
                ).fetchall()
                daily_data = [dict(r) for r in rows]

                ts_cutoff = (now - timedelta(days=days)).isoformat()

                rows = conn.execute(
                    """
                    SELECT query, COUNT(*) as count, ROUND(AVG(result_count),1) as avg_results
                    FROM search_queries WHERE timestamp >= ?
                    GROUP BY LOWER(query) ORDER BY count DESC LIMIT 30
                    """,
                    (ts_cutoff,),
                ).fetchall()
                top_searches = [
                    {"query": r["query"], "count": r["count"], "avg_results": r["avg_results"]}
                    for r in rows
                ]

                rows = conn.execute(
                    """
                    SELECT query, COUNT(*) as count
                    FROM search_queries WHERE timestamp >= ? AND result_count = 0
                    GROUP BY LOWER(query) ORDER BY count DESC LIMIT 20
                    """,
                    (ts_cutoff,),
                ).fetchall()
                search_no_results = [{"query": r["query"], "count": r["count"]} for r in rows]

                rows = conn.execute(
                    "SELECT message_count FROM conversations WHERE last_activity >= ?",
                    (ts_cutoff,),
                ).fetchall()
                archived_counts = [r["message_count"] for r in rows]
                all_counts = conv_lengths + archived_counts
                if all_counts:
                    short_sessions = sum(1 for c in all_counts if c <= 2)
                    drop_off_rate = round(short_sessions / len(all_counts) * 100, 1)
                    avg_conv_length = round(sum(all_counts) / len(all_counts), 1)

                total_archived = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        except Exception as e:
            log.error(f"Stats DB error: {e}")

        return jsonify(
            ok=True,
            total_sessions=total_sessions,
            active_24h=active_24h,
            active_1h=active_1h,
            channels=channels,
            total_messages=total_messages,
            sessions_with_cart=sessions_with_cart,
            total_cart_items=total_cart_items,
            top_products=[{"name": n, "quantity": q} for n, q in top_products],
            conversion_rate=round(sessions_with_cart / max(total_sessions, 1) * 100, 1),
            avg_conversation_length=avg_conv_length,
            drop_off_rate=drop_off_rate,
            total_archived=total_archived,
            daily=daily_data,
            top_searches=top_searches,
            search_no_results=search_no_results,
        )
    except Exception as e:
        log.error(f"[admin_stats] Error: {e}")
        return jsonify(ok=False, error="Internal error"), 500


@admin_bp.route("/admin/search", methods=["GET"])
def admin_search():
    if not _require_admin():
        return jsonify(ok=False, error="Unauthorized"), 401
    try:
        q = request.args.get("q", "").strip()
        if not q or len(q) < 2:
            return jsonify(ok=False, error="Query too short"), 400

        q_lower = q.lower()
        results = []
        all_sessions = session_manager.get_all()

        for cid, s in all_sessions.items():
            match_reason = []
            name = (s.get("customer_name") or "").lower()
            email = (s.get("user_info", {}).get("wp_email") or "").lower()
            username = (s.get("user_info", {}).get("wp_username") or "").lower()
            tg_user = (s.get("user_info", {}).get("tg_username") or "").lower()

            if q_lower in name:
                match_reason.append("name")
            if q_lower in email:
                match_reason.append("email")
            if q_lower in username or q_lower in tg_user:
                match_reason.append("username")

            conv_matches = sum(
                1 for m in s.get("conversation", [])
                if q_lower in m.get("content", "").lower()
            )
            if conv_matches > 0:
                match_reason.append(f"conversation ({conv_matches}x)")

            for item in s.get("cart", []):
                if q_lower in (item.get("title") or "").lower():
                    match_reason.append("cart")
                    break

            if match_reason:
                results.append({
                    "chat_id": cid,
                    "channel": s.get("channel", "?"),
                    "customer_name": s.get("customer_name", ""),
                    "user_info": s.get("user_info", {}),
                    "match_reason": ", ".join(match_reason),
                    "message_count": s.get("message_count", 0),
                    "last_activity": s.get("last_activity", ""),
                    "is_active": True,
                })

        # Search archived conversations
        try:
            with sqlite3.connect(HISTORY_DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                like = f"%{q}%"
                rows = conn.execute(
                    """
                    SELECT chat_id, channel, customer_name, user_info, message_count,
                           last_activity, archived_at,
                           CASE WHEN customer_name LIKE ? THEN 'name'
                                WHEN user_info LIKE ? THEN 'user_info'
                                WHEN conversation LIKE ? THEN 'conversation'
                                WHEN cart LIKE ? THEN 'cart' END as match_type
                    FROM conversations
                    WHERE customer_name LIKE ? OR user_info LIKE ? OR conversation LIKE ? OR cart LIKE ?
                    ORDER BY last_activity DESC LIMIT 50
                    """,
                    (like, like, like, like, like, like, like, like),
                ).fetchall()

                for r in rows:
                    if any(x["chat_id"] == r["chat_id"] for x in results):
                        continue
                    results.append({
                        "chat_id": r["chat_id"],
                        "channel": r["channel"] or "?",
                        "customer_name": r["customer_name"] or "",
                        "user_info": json.loads(r["user_info"] or "{}"),
                        "match_reason": r["match_type"] or "content",
                        "message_count": r["message_count"] or 0,
                        "last_activity": r["last_activity"] or "",
                        "is_active": False,
                        "archived_at": r["archived_at"] or "",
                    })
        except Exception as e:
            log.error(f"Search DB error: {e}")

        results.sort(key=lambda x: x.get("last_activity", ""), reverse=True)
        return jsonify(ok=True, results=results, total=len(results), query=q)
    except Exception as e:
        log.error(f"[admin_search] Error: {e}")
        return jsonify(ok=False, error="Internal error"), 500


@admin_bp.route("/admin/history/<chat_id>", methods=["GET"])
def admin_history(chat_id):
    if not _require_admin():
        return jsonify(ok=False, error="Unauthorized"), 401
    try:
        all_sessions = session_manager.get_all()
        if chat_id in all_sessions:
            return admin_conversation(chat_id)

        with sqlite3.connect(HISTORY_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM conversations WHERE chat_id=?", (chat_id,)
            ).fetchone()
            if not row:
                return jsonify(ok=False, error="Not found"), 404

            conv = json.loads(row["conversation"] or "[]")
            clean_conv = []
            for m in conv:
                txt = m.get("content", "")
                role = m.get("role", "")
                if role == "user" and "[KUNDE]" in txt:
                    txt = txt.split("[KUNDE]")[-1].strip()
                if role == "assistant":
                    txt = re.sub(r"\s*```cart_action\n.*?\n```\s*", "", txt, flags=re.DOTALL).strip()
                    txt = re.sub(r"\s*\[SHOW_FLAVORS:\d+\]\s*", "", txt).strip()
                    txt = re.sub(r"\s*\[SHOW_QUANTITIES:\d+\]\s*", "", txt).strip()
                clean_conv.append({"role": role, "content": txt})

            return jsonify(
                ok=True,
                chat_id=chat_id,
                channel=row["channel"] or "?",
                customer_name=row["customer_name"] or "",
                user_info=json.loads(row["user_info"] or "{}"),
                cart=json.loads(row["cart"] or "[]"),
                message_count=row["message_count"] or 0,
                created_at=row["created_at"] or "",
                last_activity=row["last_activity"] or "",
                conversation=clean_conv,
                is_archived=True,
                archived_at=row["archived_at"] or "",
            )
    except Exception as e:
        log.error(f"History load error: {e}")
        return jsonify(ok=False, error="DB error"), 500


@admin_bp.route("/admin/notifications", methods=["GET"])
def admin_notifications():
    if not _require_admin():
        return jsonify(ok=False, error="Unauthorized"), 401
    try:
        since = request.args.get("since", "")
        if not since:
            since = (datetime.now() - timedelta(seconds=15)).isoformat()

        new_messages = []
        all_sessions = session_manager.get_all()

        for cid, s in all_sessions.items():
            try:
                last = datetime.fromisoformat(s.get("last_activity", "2000-01-01"))
                since_dt = datetime.fromisoformat(since)
                if last > since_dt:
                    for m in reversed(s.get("conversation", [])):
                        if m.get("role") == "user":
                            txt = m.get("content", "")
                            if "[KUNDE]" in txt:
                                txt = txt.split("[KUNDE]")[-1].strip()
                            new_messages.append({
                                "chat_id": cid,
                                "channel": s.get("channel", "?"),
                                "customer_name": s.get("customer_name", ""),
                                "message": txt[:100],
                                "timestamp": s.get("last_activity", ""),
                            })
                            break
            except Exception:
                pass

        return jsonify(ok=True, messages=new_messages, since=since, now=datetime.now().isoformat())
    except Exception as e:
        log.error(f"[admin_notifications] Error: {e}")
        return jsonify(ok=False, error="Internal error"), 500


@admin_bp.route("/admin/photo-test", methods=["POST"])
def admin_photo_test():
    """Diagnose: Foto-Analyse + Suchergebnis fuer eine Bild-URL testen."""
    if not _require_admin():
        return jsonify(ok=False, error="Unauthorized"), 401
    try:
        import requests as _requests
        from dp_connect_bot.services.photo_vision import describe_photo, build_photo_message, _is_shelf_request
        data = request.get_json() or {}
        url = data.get("image_url", "")
        caption = data.get("caption", "")
        resp = _requests.get(url, timeout=20)
        resp.raise_for_status()
        mime = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
        desc = describe_photo(resp.content, mime, caption)
        saved = False
        # Optional: Regal-Scan unter einer chat_id ablegen (Foto-Inventur testen)
        cid = data.get("chat_id", "")
        if cid and desc and _is_shelf_request(caption):
            from dp_connect_bot.services.shelf_inventory import save_scan
            saved = save_scan(cid, desc)
        return jsonify(ok=True, description=desc, message=build_photo_message(desc, caption), scan_saved=saved)
    except Exception as e:
        log.error(f"[admin_photo_test] Error: {e}")
        return jsonify(ok=False, error=str(e)[:200]), 500


@admin_bp.route("/admin/wa-flush", methods=["POST"])
def admin_wa_flush():
    """Gepufferte WhatsApp-Sends nachliefern (nach Meta-Stoerung)."""
    if not _require_admin():
        return jsonify(ok=False, error="Unauthorized"), 401
    try:
        from dp_connect_bot.services.send_queue import flush
        return jsonify(ok=True, **flush())
    except Exception as e:
        log.error(f"[admin_wa_flush] Error: {e}")
        return jsonify(ok=False, error="Internal error"), 500


@admin_bp.route("/admin/cleanup-empty-sessions", methods=["POST"])
def admin_cleanup_empty_sessions():
    """Loescht leere Web-Sessions (0 Nachrichten) — Crawler-Muell."""
    if not _require_admin():
        return jsonify(ok=False, error="Unauthorized"), 401
    try:
        from dp_connect_bot.models.session import session_manager
        deleted = 0
        for chat_id, session in list(session_manager.get_all().items()):
            if (session.get("channel") == "web"
                    and session.get("message_count", 0) == 0
                    and not session.get("conversation")):
                session_manager.delete(chat_id)
                deleted += 1
        log.info(f"[admin_cleanup] {deleted} leere Web-Sessions geloescht")
        return jsonify(ok=True, deleted=deleted)
    except Exception as e:
        log.error(f"[admin_cleanup] Error: {e}")
        return jsonify(ok=False, error="Internal error"), 500


@admin_bp.route("/admin/visual-index", methods=["POST"])
def admin_visual_index():
    """Indexiert die naechsten N Produktbilder (Claude Vision, einmalig)."""
    if not _require_admin():
        return jsonify(ok=False, error="Unauthorized"), 401
    try:
        from dp_connect_bot.services.visual_index import index_batch
        data = request.get_json() or {}
        batch = min(int(data.get("batch", 20)), 50)
        result = index_batch(batch)
        return jsonify(ok=True, **result)
    except Exception as e:
        log.error(f"[admin_visual_index] Error: {e}")
        return jsonify(ok=False, error="Internal error"), 500


@admin_bp.route("/admin/config", methods=["GET", "POST"])
def admin_config():
    """Get or update global bot configuration."""
    if not _require_admin():
        return jsonify(ok=False, error="Unauthorized"), 401
    try:
        if request.method == "GET":
            config = load_bot_config()
            effective = {ch: get_channel_config(ch) for ch in CHANNELS}
            return jsonify(ok=True, config=config, effective=effective)

        # POST – update config
        data = request.get_json() or {}
        config = load_bot_config()
        if "order_enabled" in data:
            config["order_enabled"] = bool(data["order_enabled"])
        if "chat_checkout_enabled" in data:
            config["chat_checkout_enabled"] = bool(data["chat_checkout_enabled"])
        if "webchat_require_signed_auth" in data:
            config["webchat_require_signed_auth"] = bool(data["webchat_require_signed_auth"])
        if "restock_wa_template" in data:
            config["restock_wa_template"] = str(data["restock_wa_template"] or "")[:100]
        if "restock_wa_lang" in data:
            config["restock_wa_lang"] = str(data["restock_wa_lang"] or "de")[:10]
        if "reorder_reminders_enabled" in data:
            config["reorder_reminders_enabled"] = bool(data["reorder_reminders_enabled"])
        if "reorder_wa_template" in data:
            config["reorder_wa_template"] = str(data["reorder_wa_template"] or "")[:100]
        if "reorder_wa_lang" in data:
            config["reorder_wa_lang"] = str(data["reorder_wa_lang"] or "de")[:10]
        # Per-channel overrides: {"channels": {"telegram": {"enabled": false, ...}}}
        if isinstance(data.get("channels"), dict):
            channels_cfg = config.setdefault("channels", {})
            for ch_name, flags in data["channels"].items():
                if ch_name not in CHANNELS or not isinstance(flags, dict):
                    continue
                ch_cfg = channels_cfg.setdefault(ch_name, {})
                for flag in CHANNEL_BOOL_FLAGS:
                    if flag in flags:
                        ch_cfg[flag] = bool(flags[flag])
                if "disabled_message" in flags:
                    ch_cfg["disabled_message"] = str(flags["disabled_message"] or "")[:500]
        save_bot_config(config)
        log.info(f"[admin_config] Config updated: {config}")
        effective = {ch: get_channel_config(ch) for ch in CHANNELS}
        return jsonify(ok=True, config=config, effective=effective)
    except Exception as e:
        log.error(f"[admin_config] Error: {e}")
        return jsonify(ok=False, error="Internal error"), 500
