"""
DP Connect Bot – Configuration
===============================
All environment variables, constants, and word sets.
"""

import os
import logging

# ============================================================
# API KEYS & EXTERNAL SERVICES
# ============================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT", "")
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "")
AIRTABLE_TABLE_ID = os.environ.get("AIRTABLE_TABLE_ID", "")
AIRTABLE_VIEW_AVAILABLE = os.environ.get("AIRTABLE_VIEW_AVAILABLE", "")
AIRTABLE_VIEW_ALL = os.environ.get("AIRTABLE_VIEW_ALL", "")
WOOCOMMERCE_URL = os.environ.get("WOOCOMMERCE_URL", "https://dpconnect.de")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY", "")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET", "")

# WhatsApp Config
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "dpconnect_bot_verify_2024")
WHATSAPP_API = "https://graph.facebook.com/v18.0"

# Web Chat Config
WEBCHAT_SECRET = os.environ.get("WEBCHAT_SECRET", "dpconnect_webchat_secret_2024")

# Admin Dashboard API
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")

# OpenAI API (Whisper Voice-to-Text)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# ============================================================
# DERIVED CONFIG
# ============================================================

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
CACHE_REFRESH_MINUTES = 15
SESSION_TIMEOUT_HOURS = 24

# Paths
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION_DB_PATH = os.path.join(_BASE_DIR, "sessions.db")
HISTORY_DB_PATH = os.path.join(_BASE_DIR, "bot_history.db")
SESSION_FILE = os.path.join(_BASE_DIR, "sessions.json")  # Legacy, for migration only

# CORS
ALLOWED_ORIGINS = ["https://dpconnect.de", "https://www.dpconnect.de", "http://localhost"]

# ============================================================
# WORD SETS
# ============================================================

# Zentrale Bestaetigungswoerter
CONFIRM_YES = {"ja", "jo", "jap", "jup", "jop", "jawoll", "jawohl", "yes", "yeah", "yep", "yup",
               "ok", "oke", "okey", "okay", "okk", "okee",
               "passt", "genau", "klar", "sicher", "logo", "gut", "gerne",
               "mach das", "ja bitte", "ja mach", "ja gerne",
               "si", "safe", "alles klar", "geht klar", "mach", "bitte",
               "stimmt", "richtig", "korrekt", "jaa", "jaaa", "joo", "jooo",
               "top", "super", "perfekt", "cool", "nice", "mega", "geil",
               "mach mal", "pack ein", "los", "weiter", "go"}
CONFIRM_NO = {"nein", "nö", "ne", "nee", "nope", "no", "nicht", "lieber nicht",
              "doch nicht", "lass mal", "stop", "stopp", "abbrechen", "cancel"}
CONFIRM_ALL = CONFIRM_YES | CONFIRM_NO | {"danke", "dankeschön", "merci",
              "tschüss", "bye", "ciao", "bis dann"}

# Checkout-Trigger
CHECKOUT_WORDS = {"fertig", "bestellen", "abschließen", "abschliessen", "checkout",
                  "bezahlen", "kaufen", "das wars", "das war's", "das wärs",
                  "reicht", "genug", "nix mehr", "nichts mehr", "bin fertig",
                  "will bestellen", "möchte bestellen", "order", "kasse"}

# Warenkorb-Anzeige Trigger
CART_DISPLAY_WORDS = {"warenkorb", "cart", "was hab ich", "was habe ich", "was ist drin", "übersicht"}

# Nachbestell-Trigger
REORDER_TRIGGERS = {"nochmal", "nochmal bestellen", "wie letztes mal", "wie beim letzten mal",
                    "das gleiche", "dasselbe", "gleiche bestellung", "letzte bestellung",
                    "nochmal das gleiche", "wieder das gleiche", "same again", "reorder",
                    "wie vorher", "wie gehabt", "nachbestellen", "nachbestellung"}

# Browse-Trigger
BROWSE_TRIGGERS = {"was habt ihr", "was gibt es", "was gibts", "sortiment", "was kann ich bestellen",
                   "was bietet ihr an", "was habt ihr so", "zeig mir alles", "was gibt's",
                   "was verkauft ihr", "was führt ihr", "produkte", "was habt ihr da",
                   "was hast du", "was hast du so", "was gibts so"}

# Kategorie-Mapping fuer Buttons
CATEGORY_MAP = {
    "cat_einweg": "Einweg Vapes",
    "cat_pods": "Pods",
    "cat_liquid": "Liquid",
    "cat_tabak": "Shisha Tabak",
    "cat_snacks": "Snacks",
    "cat_drinks": "Drinks",
}

# ============================================================
# BETA MODE
# ============================================================

BETA_MODE = True
BETA_HINT = ("\n\n_*Dieser Bot befindet sich in einer frühen Testphase. "
             "Fehler bitte entschuldigen! Auf dpconnect.de kannst du ganz normal stöbern und bestellen._"
             if BETA_MODE else "")
BETA_HINT_PLAIN = ("\n\n*Dieser Bot befindet sich in einer frühen Testphase. "
                   "Fehler bitte entschuldigen! Auf dpconnect.de kannst du ganz normal stöbern und bestellen."
                   if BETA_MODE else "")

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("dpconnect_bot")
