"""
DP Connect Bot – Flask App Factory
"""

from flask import Flask, request

from dp_connect_bot.config import ALLOWED_ORIGINS, SESSION_FILE, log
from dp_connect_bot.models.session import session_manager
from dp_connect_bot.services.product_cache import ensure_cache, set_on_cache_loaded
from dp_connect_bot.services.history import init_history_db
from dp_connect_bot.services.restock_watch import check_and_notify


def create_app():
    """Create and configure the Flask application."""
    app = Flask(__name__)

    # --- CORS Middleware ---
    @app.after_request
    def add_cors_headers(response):
        origin = request.headers.get("Origin", "")
        if any(origin.startswith(a) for a in ALLOWED_ORIGINS):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
        return response

    # Register blueprints
    from dp_connect_bot.routes.telegram import telegram_bp
    from dp_connect_bot.routes.whatsapp import whatsapp_bp
    from dp_connect_bot.routes.webchat import webchat_bp
    from dp_connect_bot.routes.admin import admin_bp
    from dp_connect_bot.routes.health import health_bp

    app.register_blueprint(telegram_bp)
    app.register_blueprint(whatsapp_bp)
    app.register_blueprint(webchat_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(health_bp)

    # Wieder-da-Alarm: nach jedem Cache-Reload (~15 Min) Restock pruefen + Vormerker
    # benachrichtigen. Laeuft im selben Hook, kein eigener Scheduler noetig.
    set_on_cache_loaded(check_and_notify)

    # Startup tasks
    with app.app_context():
        init_history_db()
        ensure_cache()
        # One-time migration from legacy sessions.json
        session_manager.migrate_from_json(SESSION_FILE)

    log.info("DP Connect Bot app created")
    return app
