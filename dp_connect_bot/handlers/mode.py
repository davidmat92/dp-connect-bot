"""
Mode detection & gate – determines order vs support mode.
"""

from dp_connect_bot.config import BETA_HINT, log
from dp_connect_bot.models.response import BotResponse, Keyboard, KeyboardType
from dp_connect_bot.utils.hints import get_hint


# Produktsignale fuer Smart Detection
PRODUCT_KEYWORDS = {
    "elf bar", "elfbar", "elfa", "flerbar", "lost mary", "pods", "pod",
    "liquid", "vape", "einweg", "tabak", "shisha", "snacks", "drinks",
    "crystal", "köpfe", "dampfe", "energy", "kohle", "nikotinfrei",
    "bestellen", "bestell", "brauche", "brauch", "hätte gerne",
    "was habt ihr", "was gibt es", "zeig mir", "sortiment",
    "elf", "fler", "lost", "ske", "rand m", "al fakher",
    "nochmal", "nachbestellen", "das gleiche", "wie letztes mal",
    # Sortiments-/Kauf-Signale (vage Anfragen sollen NICHT im Menü landen)
    "ohne nikotin", "nikotin", "fruchtig", "geschmack", "geschmäcker",
    "sorte", "sorten", "habt ihr", "hast du", "gibt es", "gibts", "gibt's",
    "auf lager", "lieferbar", "verfügbar", "verfuegbar", "vorrätig",
    "kostet", "preis", "preise", "angebot", "für den laden", "fuer den laden",
    "kiosk", "empfehlung", "empfehlen", "bestseller", "was neues", "neuheiten",
}

# Reine Begruessungen ohne Anliegen → Modus-Menue zur Orientierung. Alles andere
# (auch kurze Produktfragen wie "panini da?", deren Name nicht in PRODUCT_KEYWORDS
# steht) geht an die Bestell-KI, die wirklich suchen + antworten kann.
GREETINGS = {
    "hi", "hallo", "hey", "moin", "moin moin", "servus", "na", "yo", "jo",
    "tach", "hello", "hej", "huhu", "hallöchen", "hallöle", "hi du", "hey du",
    "hallo du", "grüß dich", "gruß dich", "grüß gott", "gruss gott",
    "guten morgen", "guten tag", "guten abend", "schönen guten tag", "moinsen",
}

# Support-Signale fuer Smart Detection
SUPPORT_KEYWORDS = {
    "wo bleibt", "wo ist meine bestellung", "bestellung verfolgen",
    "tracking", "sendungsverfolgung", "lieferstatus",
    "mein paket", "paket nicht angekommen", "paket noch nicht", "wo ist das paket",
    "verschickt", "versandt", "meine lieferung", "versand status",
    "einloggen", "login", "anmelden", "kann mich nicht einloggen",
    "passwort", "kennwort", "registrierung", "registrieren",
    "keine zugangsdaten", "zugangsdaten",
    "rechnung", "invoice", "rechnungsadresse",
    "lieferadresse", "adresse ändern", "adresse aendern",
    "reklamation", "retoure", "rücksendung", "ruecksendung",
    "defekt", "kaputt", "beschaedigt", "beschädigt",
    "kundenservice", "support", "hilfe", "problem",
    "kundennummer", "mein konto", "mein account",
    "mit davide sprechen", "mit jemandem sprechen",
    "ich will einen menschen", "mitarbeiter",
}

# Self-Service-Signale: verifizierte Kunden, die ihre EIGENEN Bestellungen/
# Rechnungen abrufen wollen → Order-Modus (Tools), nicht Support.
# Echte Probleme (Reklamation/Login/Tracking) gehen weiter zum Support.
SELF_SERVICE_KEYWORDS = {
    "meine bestellung", "meine bestellungen", "letzte bestellung",
    "letzten bestellung", "letzte bestellungen", "bestellverlauf",
    "bestellhistorie", "meine rechnung", "rechnung schicken",
    "schick mir die rechnung", "schick mir meine rechnung", "rechnung zur",
    "rechnung von", "rechnung haben", "bestellstatus", "was hab ich bestellt",
    # Weitere natürliche Rechnungs-Formulierungen (sonst landet "meine letzte
    # rechnung geben" im Support-Bot, der kein get_invoice hat):
    "die rechnung", "letzte rechnung", "letzten rechnung", "rechnung geben",
    "rechnung bekommen", "rechnung kriegen", "rechnung runter", "rechnung herunter",
    "rechnung als pdf", "rechnung bitte", "brauche meine rechnung", "brauche die rechnung",
    "was habe ich bestellt", "offene rechnung", "offene rechnungen",
    "offene posten", "was muss ich noch zahlen", "was schulde ich",
    "meine rechnungen", "welche rechnungen",
    # Sendungsverfolgung (verifizierte Kunden → Self-Service-Tracking,
    # kein Support-Umweg mit E-Mail-Abfrage)
    "wo bleibt meine bestellung", "wo ist meine bestellung", "wo ist mein paket",
    "wo bleibt mein paket", "sendungsverfolgung", "tracking", "lieferstatus",
    "bestellung verfolgen", "schon verschickt", "schon raus", "schon unterwegs",
    "wann kommt meine bestellung", "wann kommt mein paket",
}

# Signale, die AUCH mitten im Bestell-Modus zum Support wechseln —
# sonst sitzt der Kunde im Bestell-Modus fest und erreicht nie einen Menschen.
ESCALATION_KEYWORDS = {
    "mensch", "mitarbeiter", "mit davide", "mit jemandem sprechen",
    "kundenservice", "echten support", "echter support",
    "reklamation", "retoure", "defekt", "kaputt", "beschädigt", "beschaedigt",
    "beschwerde", "verstehst mich nicht", "verstehst du nicht",
    "kapierst du nicht", "du verstehst nicht",
}


def detect_mode(session, text, channel):
    """Smart mode detection. Returns BotResponse if mode gate should block, else None.

    Side effect: sets session["mode"] if detected.
    """
    from dp_connect_bot.services.bot_config import channel_flag
    order_enabled = channel_flag(channel, "order_enabled")

    # Handle "choosing" state: user typed text instead of clicking a button
    if session.get("mode") == "choosing":
        lower = text.strip().lower()
        if any(kw in lower for kw in SUPPORT_KEYWORDS):
            session["mode"] = "support"
            session["support_step"] = None
        elif order_enabled:
            session["mode"] = "order"
        else:
            # Order disabled → fall into support
            session["mode"] = "support"
            session["support_step"] = None
        return None  # Let the message pass through to the detected handler

    if session.get("mode") is not None:
        lower_now = text.strip().lower()
        # Auch im laufenden Bestell-Modus: Eskalations-Signale → Support
        if session.get("mode") == "order":
            if any(kw in lower_now for kw in ESCALATION_KEYWORDS):
                log.info("Eskalations-Signal im Bestell-Modus → wechsle zu Support")
                session["mode"] = "support"
                session["support_step"] = None
        # Gegenrichtung: Kunde im Support will (wieder) bestellen → Bestell-Modus.
        # Ohne diesen Rueckweg sitzt er im Support fest und erreicht nie den
        # Warenkorb (Support-Bot hat keine Bestell-Tools). Nur bei KLAREM
        # Kauf-Wunsch UND ohne gleichzeitiges Support-/Beschwerde-Signal —
        # sonst wuerde eine laufende Reklamation faelschlich abgebrochen.
        elif session.get("mode") == "support" and order_enabled:
            has_purchase_intent = (
                any(w in lower_now.split() for w in
                    ("bestellen", "bestell", "kaufen", "nachbestellen", "warenkorb"))
                or "in den warenkorb" in lower_now
                or "zur kasse" in lower_now
                or "neue bestellung" in lower_now
                or "etwas bestellen" in lower_now
                or "was bestellen" in lower_now
                or "nochmal das gleiche" in lower_now
            )
            # Klare Bestellung getippt ("50 elf bar cherry"): Zahl + Produktsignal
            qty_order = (any(c.isdigit() for c in lower_now)
                         and any(kw in lower_now for kw in PRODUCT_KEYWORDS))
            still_support = (any(kw in lower_now for kw in ESCALATION_KEYWORDS)
                             or any(kw in lower_now for kw in SUPPORT_KEYWORDS))
            if (has_purchase_intent or qty_order) and not still_support:
                log.info("Kauf-Wunsch im Support-Modus → wechsle zu Bestell-Modus")
                session["mode"] = "order"
        return None

    if text.strip().startswith("/"):
        return None

    lower = text.strip().lower()

    # Eigenes Bestell-/Rechnungs-/Tracking-Anliegen ("meine Rechnung", "letzte
    # Bestellung", "wo ist mein Paket") → IMMER Order-Modus — auch UNVERIFIZIERT.
    # Nur dort liegen die Self-Service-Tools (lookup_my_orders/get_invoice/
    # track_my_order). Der Order-Bot liefert es verifizierten Kunden direkt und
    # verifiziert unverifizierte kurz (E-Mail→Code) und liefert es DANN hier.
    # WICHTIG: Der Support-Bot hat KEIN get_invoice — landete eine Rechnungs-Anfrage
    # dort (weil "rechnung" auch ein SUPPORT_KEYWORD ist), wimmelte er nur ab.
    if any(kw in lower for kw in SELF_SERVICE_KEYWORDS):
        session["mode"] = "order"
        return None

    # Detect support signals (check first – support takes priority over order)
    support_signals = any(kw in lower for kw in SUPPORT_KEYWORDS)
    if support_signals:
        session["mode"] = "support"
        session["support_step"] = None
        return None  # Let the message pass through to support handling

    # Detect product/order signals
    order_signals = any(kw in lower for kw in PRODUCT_KEYWORDS)

    # Checkout-/Warenkorb-Intent ist ebenfalls Bestell-Kontext (nicht Menue)
    if not order_signals and any(w in lower.split() for w in
                                 ("kasse", "checkout", "bestellen", "warenkorb")):
        order_signals = True

    # Numbers + words = likely an order (e.g. "50 cherry")
    if not order_signals and any(c.isdigit() for c in lower) and len(lower.split()) >= 2:
        order_signals = True

    # Tippfehler-Rettung: korrigierte Fassung pruefen, bevor das Menue kommt
    # ("habt ir efbar witermelone" → "habt ihr elfbar watermelon")
    if not order_signals:
        try:
            from dp_connect_bot.services.fuzzy_matching import fuzzy_correct_text, fuzzy_match_brand
            corrected = fuzzy_match_brand(fuzzy_correct_text(lower))
            if corrected != lower and any(kw in corrected for kw in PRODUCT_KEYWORDS):
                log.info(f"Tippfehler-Rettung: {lower!r} → {corrected!r} → order")
                order_signals = True
        except Exception:
            pass

    if order_signals:
        if order_enabled:
            session["mode"] = "order"
        else:
            # Order disabled → route to support instead
            session["mode"] = "support"
            session["support_step"] = None
        return None

    if session.get("message_count", 0) <= 1:
        # Erste Nachricht ohne erkanntes Produkt-/Support-Signal.
        # NUR eine reine Begruessung ("hi", "moin") → Modus-Menue zur Orientierung.
        # Alles MIT Inhalt — auch kurze Produktfragen wie "panini da?", deren Name
        # nicht in PRODUCT_KEYWORDS steht — geht an die Bestell-KI, die wirklich
        # SUCHEN + antworten kann. (Frueher wurden Gaeste pauschal in die Login-
        # Hilfe geschickt → Produktfragen landeten faelschlich beim Login.) Login
        # bleibt ueber Support-Keywords ("einloggen"/"passwort"/…) + Menue erreichbar.
        g = lower.strip(" !?.,")
        is_greeting = g in GREETINGS or len(g) <= 2
        if not is_greeting:
            if order_enabled:
                session["mode"] = "order"
            else:
                session["mode"] = "support"
                session["support_step"] = None
            return None

        name = session.get("customer_name", "")
        session["mode"] = "choosing"
        return BotResponse(
            text=f"Hey{' ' + name if name else ''}! 👋\n\nWie kann ich dir helfen?{BETA_HINT}",
            keyboards=[Keyboard(type=KeyboardType.MODE_CHOICE)],
        )

    # Subsequent messages without mode → assume order (if enabled) or support
    if order_enabled:
        session["mode"] = "order"
    else:
        session["mode"] = "support"
        session["support_step"] = None
    return None


def handle_whatsapp_mode_choice(session, text):
    """Handle WhatsApp text-based mode selection (1, 2 or 3) as fallback.

    Returns BotResponse or None if not a mode choice.
    """
    if session.get("mode") != "choosing":
        return None

    from dp_connect_bot.services.bot_config import channel_flag
    order_enabled = channel_flag("whatsapp", "order_enabled")

    stripped = text.strip()
    if stripped == "1" and order_enabled:
        session["mode"] = "order"
        voice_hint = get_hint(session, "voice_available")
        return BotResponse(
            text=(
                "🛒 *Bestell-Assistent* aktiv!\n\n"
                "Sag mir einfach was du brauchst, z.B.:\n"
                "• \"Elf Bar 800\"\n"
                "• \"50 Cherry und 30 Peach\"\n\n"
                f"Was darf's sein? 🚀{voice_hint}"
            )
        )
    elif stripped == "1" and not order_enabled:
        # Order disabled – treat as support
        session["mode"] = "support"
        session["support_step"] = None
        return BotResponse(
            text=(
                "🎧 *Kundenservice*\n\n"
                "Der Bestellassistent ist aktuell nicht verfügbar. "
                "Wie kann ich dir sonst helfen? ✍️"
            )
        )
    elif stripped == "2":
        session["mode"] = "support"
        session["support_step"] = None
        return BotResponse(
            text=(
                "🎧 *Kundenservice*\n\n"
                "Klar, wie kann ich dir helfen? Beschreib mir einfach dein Anliegen – "
                "ich kann z.B. Bestellungen nachschlagen, Account-Probleme loesen "
                "oder dich an Davides Team weiterleiten. ✍️"
            )
        )
    elif stripped == "3":
        session["mode"] = "login_help"
        session["login_step"] = "ask_email"
        return BotResponse(
            text=(
                "🔑 *Login-Hilfe*\n\n"
                "Klar! Was ist deine E-Mail-Adresse, mit der du registriert bist? ✉️"
            )
        )

    return None


def is_human_mode(session):
    """Check if the session is in human takeover mode."""
    return session.get("human_mode", False)
