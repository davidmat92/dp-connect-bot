"""
Claude AI service – loads system prompt, calls Anthropic API.
Supports both order mode (simple text) and support mode (tool use).
"""

import json
import os
import re
import time
import requests

from dp_connect_bot.config import ANTHROPIC_API_KEY, CLAUDE_MODEL, log
from dp_connect_bot.utils.formatting import parse_price

# Rechnungslinks (langer Zufalls-Hash) NIE vom Modell ausgeben lassen — LLMs
# verändern solche Tokens beim Abtippen → kaputte URL. Wir entfernen jede vom
# Modell geschriebene rechnung.dpconnect.de-URL und haengen den EXAKTEN Link aus
# dem Tool-Ergebnis im Code an (siehe get_invoice + call_claude).
_INVOICE_URL_RE = re.compile(r'https?://rechnung\.dpconnect\.de/\S+', re.IGNORECASE)


# ============================================================
# PROMPTS
# ============================================================

_PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
_PROMPT_PATH = os.path.join(_PROMPT_DIR, "system_prompt.md")
_SUPPORT_PROMPT_PATH = os.path.join(_PROMPT_DIR, "support_prompt.md")

try:
    with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read()
except FileNotFoundError:
    log.error(f"System prompt nicht gefunden: {_PROMPT_PATH}")
    SYSTEM_PROMPT = "Du bist ein Verkaufsassistent fuer DP Connect."

try:
    with open(_SUPPORT_PROMPT_PATH, "r", encoding="utf-8") as f:
        SUPPORT_PROMPT = f.read()
except FileNotFoundError:
    log.error(f"Support prompt nicht gefunden: {_SUPPORT_PROMPT_PATH}")
    SUPPORT_PROMPT = "Du bist der Kundenservice-Bot von DP Connect."


# ============================================================
# ANTHROPIC API HELPERS
# ============================================================

_API_URL = "https://api.anthropic.com/v1/messages"
_API_HEADERS = {
    "content-type": "application/json",
    "anthropic-version": "2023-06-01",
}


def _api_call(system, messages, tools=None, max_tokens=2000):
    """Low-level Anthropic API call. Returns parsed JSON or None."""
    if not ANTHROPIC_API_KEY:
        return None

    headers = {**_API_HEADERS, "x-api-key": ANTHROPIC_API_KEY}
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
        # Sonnet 4.6 default-effort ist "high" → fuer Chat-Latenz auf medium
        "output_config": {"effort": "medium"},
    }
    if tools:
        payload["tools"] = tools

    resp = requests.post(_API_URL, headers=headers, json=payload, timeout=30)
    # Harter API-Ausfall (z.B. Guthaben leer) → einmalig Davide alarmieren,
    # damit der Bot nicht still fuer alle Kunden ausfaellt.
    if resp.status_code in (400, 401, 402, 429) and "credit balance" in resp.text.lower():
        try:
            from dp_connect_bot.services.pushover import notify_api_outage
            notify_api_outage("Anthropic-Guthaben aufgebraucht (HTTP 400 'credit balance too low').")
        except Exception as _e:
            log.error(f"outage-alert fehlgeschlagen: {_e}")
    resp.raise_for_status()
    return resp.json()


def _sanitize_messages(msgs):
    """Macht das Message-Array API-tauglich: beginnt mit 'user' und die Rollen
    wechseln strikt ab (Anthropic verlangt das, sonst HTTP 400 → Kunde bekommt
    den Fehler-Fallback). Schuetzt gegen Handler, die unpaarige Eintraege
    anhaengen (z.B. der human_mode-Block haengt NUR eine user-Message an) und
    gegen History-Fenster, die mitten in einem Paar starten. Nur fuer
    Text-Messages — die Tool-Bloecke im Loop sind per Konstruktion korrekt gepaart."""
    out = []
    for m in msgs:
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant") or not content:
            continue
        if not out:
            if role != "user":
                continue  # fuehrende Assistant-Message(s) verwerfen
            out.append({"role": role, "content": content})
        elif out[-1]["role"] == role:
            # gleiche Rolle direkt hintereinander → zusammenfassen statt 400
            if isinstance(out[-1]["content"], str) and isinstance(content, str):
                out[-1]["content"] += "\n" + content
            else:
                out[-1] = {"role": role, "content": content}
        else:
            out.append({"role": role, "content": content})
    return out


# ============================================================
# ORDER MODE
# ============================================================

# Such-Tools fuer den Bestell-Modus: Claude kann den Live-Katalog selbst
# durchsuchen statt sich allein auf den heuristischen Vorab-Kontext zu
# verlassen (Fuzzy-Matching kann danebenliegen, vage Anfragen abdecken).
ORDER_TOOLS = [
    {
        "name": "search_products",
        "description": (
            "Durchsucht den Live-Produktkatalog (mit Lagerbestand und Preisen). "
            "Nutze dieses Tool wenn die [PRODUKTDATEN] leer sind, nicht zu dem passen "
            "was der Kunde sucht, oder du eine alternative Schreibweise, Marke oder "
            "Kategorie probieren willst. Suche mit Marken-/Produktnamen oder Kategorie "
            "(z.B. 'elfliq', 'elf bar 800', 'shisha tabak') — OHNE Mengen oder Stueckzahlen. "
            "Findet auch optische Beschreibungen der Verpackung: 'tier', 'totenkopf', "
            "'drache', 'frucht melone' etc. ('das Liquid mit dem Tier drauf')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Suchbegriff: Marke, Produktname oder Kategorie. Keine Mengenangaben.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_product_variants",
        "description": (
            "Listet ALLE Varianten (Geschmaecker/Staerken/Farben) eines Produkts mit "
            "Verfuegbarkeit, Preisen und IDs. Nutze es wenn der Kunde nach der Auswahl "
            "eines konkreten Produkts fragt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "string",
                    "description": "Produkt-ID des Parent-Produkts (aus [ID:...]).",
                }
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "list_categories",
        "description": "Uebersicht aller Produktkategorien mit Produktanzahl. Fuer 'was habt ihr so?'-Fragen.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "lookup_my_orders",
        "description": (
            "Zeigt Bestellungen DES AKTUELLEN KUNDEN (Nummer, Datum, Status, Betrag, "
            "Positionen). Nutze es bei 'meine letzten Bestellungen', 'was hab ich letztes "
            "Mal bestellt', 'Bestellstatus'. Fuer AELTERE Bestellungen ('zeig mir mehr', "
            "'noch aeltere') erhoehe page (2, 3, ...). Nur fuer verifizierte Kunden."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Anzahl pro Seite (1-20, Default 5)."},
                "page": {"type": "integer", "description": "Seite fuer aeltere Bestellungen (Default 1)."}
            },
        },
    },
    {
        "name": "get_order_detail",
        "description": (
            "Zeigt ALLE Positionen + Status einer BESTIMMTEN Bestellung DES AKTUELLEN "
            "KUNDEN per Bestellnummer. Nutze es bei 'was war in Bestellung 8912', 'zeig mir "
            "die Bestellung vom April', wenn der Kunde eine konkrete (auch alte) Bestellung "
            "im Detail sehen will. Nur fuer verifizierte Kunden."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Bestellnummer."}
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "get_invoice",
        "description": (
            "Holt einen Link zur Rechnung einer Bestellung DES AKTUELLEN KUNDEN. Nutze es bei "
            "'schick mir die Rechnung', 'Rechnung zur Bestellung 10215'. Ohne order_id wird die "
            "letzte Bestellung genommen. Funktioniert nur fuer verifizierte Kunden."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Bestellnummer (optional, aus dem Verlauf)."}
            },
        },
    },
    {
        "name": "track_my_order",
        "description": (
            "Zeigt Sendungsstatus + DHL-Tracking einer Bestellung DES AKTUELLEN KUNDEN. "
            "Nutze es bei 'wo bleibt meine Bestellung', 'wo ist mein Paket', 'Sendungsverfolgung', "
            "'ist meine Bestellung schon raus'. Ohne order_id die letzte Bestellung. "
            "Nur fuer verifizierte Kunden."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Bestellnummer (optional)."}
            },
        },
    },
    {
        "name": "list_my_invoices",
        "description": (
            "Zeigt die Rechnungen DES AKTUELLEN KUNDEN mit Zahlstatus (offen/bezahlt/ueberfaellig) "
            "und offenem Betrag. Nutze es bei 'welche Rechnungen sind offen', 'was muss ich noch "
            "zahlen', 'offene Posten'. Default nur offene; only_open=false zeigt auch bezahlte. "
            "Nur fuer verifizierte Kunden."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "only_open": {"type": "boolean", "description": "Nur offene (Default true) oder alle."}
            },
        },
    },
    {
        "name": "notify_when_back",
        "description": (
            "Merkt den Kunden vor, damit er automatisch benachrichtigt wird, sobald ein "
            "Produkt (wieder) wirklich lieferbar ist ('Wieder-da-Alarm'). "
            "Nutze es, wenn der Kunde das moechte ('sag Bescheid wenn wieder da', "
            "'benachrichtige mich wenn verfuegbar') UND die Variante gerade AUSVERKAUFT "
            "ODER NUR VORBESTELLBAR ist (beides moeglich!). Bei vorbestellbaren Produkten "
            "wird erst benachrichtigt, wenn sie WIRKLICH auf Lager sind (nicht beim "
            "Platzhalter-Bestand). product_id = ID der konkreten Variante."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "ID der ausverkauften ODER vorbestellbaren Variante."}
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "reorder_suggestion",
        "description": (
            "Analysiert die Bestell-Historie des VERIFIZIERTEN Kunden: seinen "
            "Bestell-Rhythmus (alle wie viele Tage), wie lange die letzte Bestellung "
            "her ist, ob er fuer eine Nachbestellung FAELLIG ist, und seine haeufigsten "
            "Stammartikel. Nutze es, wenn der Kunde nach Nachbestellung fragt ('was soll "
            "ich nachbestellen', 'auffuellen', 'das uebliche', 'bin ich dran', 'wann "
            "hab ich zuletzt bestellt') ODER um einen Stammkunden proaktiv zu erinnern. "
            "Kein Input noetig — der Kunde steht ueber die Session fest."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "shelf_inventory",
        "description": (
            "Zeigt das ZULETZT vom Kunden geschickte Regal-Foto: welche Produkte LEER "
            "oder KNAPP waren und vor wie vielen Tagen. Nutze es bei 'was muss ich "
            "auffuellen', 'was geht mir aus', 'nachbestellen' — auch OHNE neues Foto — "
            "um an die leeren/knappen Faecher des letzten Regal-Scans zu erinnern. "
            "Kein Input noetig."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _execute_order_tool(tool_name, tool_input, session=None):
    """Fuehrt ein Bestell-Tool aus. Gibt immer einen String zurueck.

    Self-Service-Tools (lookup_my_orders/get_invoice) ziehen die customer_id
    IMMER aus der verifizierten Session — niemals aus tool_input (Sicherheit).
    """
    from dp_connect_bot.services.product_cache import cache, ensure_cache
    from dp_connect_bot.services.product_context import (
        format_search_results, format_parent_with_variations, get_category_overview,
    )
    try:
        if tool_name in ("lookup_my_orders", "get_invoice", "get_order_detail", "list_my_invoices", "track_my_order", "reorder_suggestion"):
            verified = (session or {}).get("verified") or {}
            customer_id = verified.get("customer_id")
            if not customer_id:
                return "FEHLER: Kunde nicht verifiziert — bitte erst verifizieren (E-Mail/Nummer)."

            if tool_name == "reorder_suggestion":
                from dp_connect_bot.services.reorder_engine import analyze
                r = analyze(customer_id)
                if not r.get("ok"):
                    return "Konnte die Bestell-Historie gerade nicht analysieren."
                if not r.get("enough_history"):
                    return (f"Noch zu wenig Bestell-Historie fuer eine Rhythmus-Vorhersage "
                            f"({r.get('order_count', 0)} Bestellung[en]) — biete normal eine "
                            "Nachbestellung der letzten Bestellung an, falls gewuenscht.")
                top = ", ".join(r.get("top_products", [])) or "(keine wiederkehrenden Stammartikel erkannt)"
                faellig = "JA, faellig" if r.get("due") else "noch nicht faellig"
                return (
                    f"NACHBESTELL-ANALYSE: Rhythmus ~alle {r['avg_interval_days']} Tage; "
                    f"letzte Bestellung vor {r['days_since_last']} Tagen ({r['last_order_date']}). "
                    f"Faellig: {faellig}. Haeufigste Stammartikel: {top}. "
                    "→ Wenn FAELLIG: freundlich + proaktiv in der Sprache des Kunden auf die "
                    "Nachbestellung hinweisen ('du bestellst etwa alle X Tage, deine letzte ist Y "
                    "Tage her — auffuellen?') und anbieten, die letzte Bestellung zu laden. Wenn "
                    "NICHT faellig: nicht aufdraengen, nur auf Nachfrage erwaehnen."
                )

            from dp_connect_bot.services import chat_order
            if tool_name == "lookup_my_orders":
                limit = tool_input.get("limit", 5)
                page = tool_input.get("page", 1)
                res = chat_order.get_order_history(customer_id, limit, page)
                if not res.get("ok"):
                    return "Konnte die Bestellungen gerade nicht laden."
                orders = res.get("orders", [])
                if not orders:
                    return ("Keine weiteren (aelteren) Bestellungen vorhanden." if page > 1
                            else "Der Kunde hat noch keine Bestellungen.")
                lines = []
                for o in orders:
                    items = o.get("items", [])
                    item_str = ", ".join(items[:6]) + (f" und {len(items)-6} weitere" if len(items) > 6 else "")
                    lines.append(
                        f"Bestellung #{o['number']} vom {o['date']} — {o['status']} — "
                        f"{str(o['total']).replace('.', ',')}€\n  Positionen: {item_str}"
                    )
                header = f"BESTELLUNGEN (Seite {page}):" if page > 1 else "LETZTE BESTELLUNGEN:"
                footer = "\n(Es gibt noch aeltere — page erhoehen fuer mehr.)" if res.get("has_more") else ""
                return header + "\n" + "\n".join(lines) + footer

            if tool_name == "get_order_detail":
                oid = str(tool_input.get("order_id", "")).strip()
                if not oid:
                    return "Bitte Bestellnummer angeben."
                res = chat_order.get_order_detail(customer_id, oid)
                if res.get("ok"):
                    items = "\n".join(
                        f"  - {i['quantity']}× {i['name']}" + (f" ({str(i['total']).replace('.', ',')}€)" if i.get('total') else "")
                        for i in res.get("items", [])
                    )
                    return (f"BESTELLUNG #{res['number']} vom {res['date']} — {res['status']} — "
                            f"{str(res['total']).replace('.', ',')}€ ({res.get('payment','')})\n{items}")
                # not_owner und order_not_found bewusst IDENTISCH — sonst koennte ein
                # verifizierter Kunde am unterschiedlichen Text ablesen, welche
                # Bestellnummern existieren (Enumeration). 'NICHT herausgeben' steht
                # implizit drin: es gibt schlicht keine Daten zurueck.
                same = "Diese Bestellnummer gibt es nicht oder gehoert nicht zu diesem Konto."
                reasons = {"not_owner": same, "order_not_found": same}
                return reasons.get(res.get("reason"), "Konnte die Bestellung nicht laden.")

            if tool_name == "track_my_order":
                order_id = str(tool_input.get("order_id", "")).strip() or None
                res = chat_order.get_tracking(customer_id, order_id)
                if not res.get("ok"):
                    same = "Diese Bestellnummer gibt es nicht oder gehoert nicht zu diesem Konto."
                    reasons = {
                        "no_orders": "Der Kunde hat noch keine Bestellungen.",
                        "not_owner": same,
                        "order_not_found": same,
                    }
                    return reasons.get(res.get("reason"), "Konnte den Sendungsstatus nicht abrufen.")
                out = f"BESTELLUNG #{res.get('number')} — Status: {res.get('status')}\n{res.get('status_hint', '')}"
                tr = res.get("tracking")
                if tr and tr.get("tracking_url"):
                    num = tr.get("tracking_number", "")
                    out += f"\n🚚 DHL-Sendungsverfolgung{' (Nr. ' + num + ')' if num else ''}: {tr['tracking_url']}"
                elif res.get("status_raw") == "completed":
                    out += "\n(Bestellung abgeschlossen — falls keine Sendungsnummer hinterlegt ist, wurde sie evtl. abgeholt/anders versandt.)"
                return out

            if tool_name == "list_my_invoices":
                only_open = tool_input.get("only_open", True)
                res = chat_order.get_invoices(customer_id, only_open)
                if not res.get("ok"):
                    if res.get("reason") == "no_easybill":
                        return "Rechnungssystem gerade nicht erreichbar."
                    return "Konnte die Rechnungen gerade nicht laden."
                invs = res.get("invoices", [])
                if not invs:
                    return ("Du hast aktuell KEINE offenen Rechnungen — alles bezahlt! 🎉"
                            if only_open else "Keine Rechnungen gefunden.")
                state_de = {"open": "offen", "overdue": "überfällig", "partial": "teilweise bezahlt", "paid": "bezahlt"}
                lines = []
                for i in invs:
                    due = ""
                    d = i.get("days_until_due")
                    if i["state"] == "overdue" and d is not None:
                        due = f" (seit {abs(int(d))} Tagen fällig)"
                    elif i["state"] in ("open", "partial") and d is not None and d >= 0:
                        due = f" (fällig in {int(d)} Tagen)"
                    betrag = f"{str(i['open']).replace('.', ',')}€ offen" if i["state"] != "paid" else "bezahlt"
                    lines.append(f"Rechnung {i['invoice_number']} (zu Bestellung #{i['order_number']}) — "
                                 f"{state_de.get(i['state'], i['state'])} — {betrag}{due}")
                summe = res.get("total_open", 0)
                footer = (f"\nOFFENE SUMME GESAMT: {str(summe).replace('.', ',')}€" if summe else "")
                return "RECHNUNGEN DES KUNDEN:\n" + "\n".join(lines) + footer

            # get_invoice
            order_id = str(tool_input.get("order_id", "")).strip() or None
            res = chat_order.get_invoice_link(customer_id, order_id)
            if res.get("ok") and res.get("url"):
                log.info(f"[invoice] Kunde {customer_id} Bestellung #{res.get('number')} → tools-URL: {res['url']}")
                # Link NICHT ins Tool-Ergebnis geben, das das Modell sieht — sonst tippt
                # es den Hash ab und korrumpiert ihn. Exakt im Session-State zwischen-
                # speichern; call_claude haengt ihn nach der Antwort 1:1 an.
                if session is not None:
                    session["_pending_invoice"] = {
                        "url": res["url"],
                        "number": res.get("number"),
                        "invoice_number": res.get("invoice_number", ""),
                    }
                return (f"RECHNUNG zu Bestellung #{res.get('number')} "
                        f"(Rechnungsnr. {res.get('invoice_number', '')}) ist verfügbar. "
                        "WICHTIG: Bestaetige dem Kunden NUR kurz (z.B. 'Hier ist deine Rechnung "
                        "zu Bestellung #X 📄') und schreibe SELBST KEINE URL/keinen Link — das "
                        "System haengt den korrekten Rechnungslink automatisch an.")
            same = "Diese Bestellnummer gibt es nicht oder gehoert nicht zu diesem Konto."
            reasons = {
                "no_orders": "Der Kunde hat noch keine Bestellungen.",
                "no_invoice": f"Zu Bestellung #{res.get('number','?')} gibt es noch keine Rechnung (kommt nach Abschluss).",
                "not_owner": same,
                "order_not_found": same,
                "no_easybill": "Rechnungssystem gerade nicht erreichbar.",
            }
            return reasons.get(res.get("reason"), "Konnte die Rechnung gerade nicht abrufen.")

        if tool_name == "shelf_inventory":
            # Eigene Scan-Daten des Kunden (per chat_id) — keine Verifizierung noetig.
            from dp_connect_bot.services.shelf_inventory import summary
            s = summary((session or {}).get("chat_id", ""))
            if not s:
                return ("Kein gespeichertes Regal-Foto vorhanden. Der Kunde kann eins schicken "
                        "(Foto vom Regal mit z.B. 'nachbestellen' dazu), dann merke ich mir den Bestand.")
            parts = [f"LETZTER REGAL-SCAN (vor {s['age_days']} Tagen, {s['total']} Produkte erfasst):"]
            if s.get("empty"):
                parts.append("LEER: " + ", ".join(s["empty"][:15]))
            if s.get("low"):
                parts.append("KNAPP: " + ", ".join(s["low"][:15]))
            if not s.get("empty") and not s.get("low"):
                parts.append("Alles war ausreichend gefuellt.")
            parts.append("→ Erinnere den Kunden in SEINER Sprache an die leeren/knappen Faecher und "
                         "biete an, sie nachzubestellen. Hinweis, dass das Foto evtl. nicht mehr aktuell ist.")
            return "\n".join(parts)

        if tool_name == "notify_when_back":
            pid = str(tool_input.get("product_id", "")).strip()
            if not pid:
                return "FEHLER: keine product_id."
            ensure_cache()
            prod = cache.get_product_by_id(pid)
            if not prod:
                return "Dieses Produkt finde ich gerade nicht — bitte nochmal die Variante suchen."
            is_preorder = bool(prod.get("preorder"))
            # Vorbestell-Produkte stehen technisch auf instock (Platzhalter-Bestand),
            # sind aber NICHT lieferbar → Vormerkung trotzdem erlauben. Nur GENUINE
            # Verfuegbarkeit (lieferbar UND nicht preorder) braucht keine Vormerkung.
            if cache.is_available(pid) and not is_preorder:
                return f"Gute Nachricht: {prod.get('title','Das Produkt')} ist AKTUELL vorrätig — kann direkt eingepackt werden, keine Vormerkung nötig."
            channel = (session or {}).get("channel", "")
            chat_id = (session or {}).get("chat_id", "")
            recipient = chat_id.split("_", 1)[1] if "_" in chat_id else chat_id
            if channel == "web" or not recipient:
                return ("Über den Webchat kann ich dich später leider nicht automatisch benachrichtigen "
                        "(dafür müsstest du die Seite offen lassen). Schau gern bald wieder vorbei — oder "
                        "schreib uns über WhatsApp/Telegram, dann merke ich dich vor!")
            from dp_connect_bot.services.restock_watch import add_watch
            name = (session or {}).get("customer_name", "")
            if add_watch(pid, channel, recipient, name):
                when = "lieferbar" if is_preorder else "wieder lieferbar"
                return (f"Vorgemerkt! ✅ Sobald {prod.get('title','das Produkt')} {when} ist, "
                        "melde ich mich automatisch bei dir.")
            return "Hmm, das Vormerken hat gerade nicht geklappt — versuch's bitte gleich nochmal."

        if tool_name == "search_products":
            query = str(tool_input.get("query", "")).strip()
            if not query:
                return "Leere Suchanfrage."
            result = format_search_results(query)
            # Bei 0 Treffern: Alias-normalisierte Query probieren
            # (z.B. "shisha tabak" → Kategorie heisst nur "tabak")
            if "Keine Produkte gefunden" in result:
                from dp_connect_bot.services.fuzzy_matching import normalize_query
                alt = normalize_query(query)
                if alt and alt != query.lower():
                    alt_result = format_search_results(alt)
                    if "Keine Produkte gefunden" not in alt_result:
                        return alt_result
            # Immer noch 0 und mehrwortig: Teilbegriffe einzeln probieren
            # ("pikachu vape" → Treffer fuer "pikachu" allein)
            if "Keine Produkte gefunden" in result and len(query.split()) > 1:
                for w in sorted(query.split(), key=len, reverse=True):
                    if len(w) < 4:
                        continue
                    sub = format_search_results(w)
                    if "Keine Produkte gefunden" not in sub:
                        return (f"(Keine Treffer fuer '{query}' als Ganzes — "
                                f"aber Treffer fuer den Teilbegriff '{w}':)\n{sub}")
            return result.strip() or f"Keine Produkte gefunden fuer '{query}'."

        if tool_name == "get_product_variants":
            ensure_cache()
            pid = str(tool_input.get("product_id", "")).strip()
            product = cache.get_product_by_id(pid)
            if not product:
                return f"Produkt {pid} nicht gefunden."
            if product.get("post_parent"):
                product = cache.get_product_by_id(product["post_parent"]) or product
            return format_parent_with_variations(product)

        if tool_name == "list_categories":
            return get_category_overview()

        return f"Unbekanntes Tool: {tool_name}"
    except Exception as e:
        log.error(f"Order tool error {tool_name}: {e}")
        return "Tool-Fehler, bitte ohne dieses Ergebnis weitermachen."

def call_claude(session, user_message, product_context="", wc_cart=None):
    """Ruft Claude API auf und gibt die Antwort zurueck.

    Args:
        session: Session dict (with conversation, cart)
        user_message: Nachricht des Kunden
        product_context: Produktdaten-String von build_product_context
        wc_cart: WooCommerce Cart vom Frontend (optional, fuer Webchat)

    Returns:
        AI response text string
    """
    if not ANTHROPIC_API_KEY:
        return "Bot ist noch nicht konfiguriert (API Key fehlt). Bitte Admin kontaktieren."

    messages = list(session["conversation"][-24:])

    # Warenkorb IMMER mitschicken
    if wc_cart and isinstance(wc_cart, list) and len(wc_cart) > 0:
        cart_lines = []
        cart_total = 0.0
        for item in wc_cart:
            # Defensiv parsen — dieser Block laeuft VOR dem try/except, ein
            # ungueltiger Frontend-Preis ("5,30") oder eine kaputte Menge wuerde
            # sonst die ganze Antwort crashen (HTTP 500, Nachricht verloren).
            p = parse_price(item.get("price", 0))
            try:
                qty = int(float(item.get("quantity", 1)))
            except (ValueError, TypeError):
                qty = 1
            sub = p * qty
            cart_total += sub
            name = item.get("name", "?")
            pid = item.get("product_id") or item.get("variation_id") or "?"
            cart_lines.append(f"  - {name} x{qty} à {p:.2f}€ = {sub:.2f}€ [ID:{pid}]")
        cart_str = f"AKTUELLER WARENKORB ({len(wc_cart)} Positionen, Gesamt: {cart_total:.2f}€ netto):\n" + "\n".join(cart_lines)
    elif session["cart"]:
        cart_lines = []
        cart_total = 0.0
        for item in session["cart"]:
            p = parse_price(item.get("price"))
            sub = p * item["quantity"]
            cart_total += sub
            cart_lines.append(f"  - {item['title']} x{item['quantity']} à {p}€ = {sub:.2f}€ [ID:{item['product_id']}]")
        cart_str = "AKTUELLER WARENKORB (" + str(len(session["cart"])) + " Produkte, Gesamt: " + f"{cart_total:.2f}€ netto):\n" + "\n".join(cart_lines)
    else:
        cart_str = "WARENKORB: Leer"

    # Kundenstatus fuer die KI (Interessenten-Modus vs. verifizierter Kunde)
    from dp_connect_bot.services.verification import is_verified, strip_prices as _strip_prices
    _verified = is_verified(session)
    if _verified:
        v = session.get("verified") or {}
        who = ", ".join(x for x in [v.get("name"), v.get("firma")] if x)
        status_str = f"KUNDENSTATUS: VERIFIZIERTER B2B-KUNDE{' (' + who + ')' if who else ''}"
    else:
        status_str = ("KUNDENSTATUS: NICHT VERIFIZIERT (Interessenten-Modus) — "
                      "KEINE Preise nennen, kein Warenkorb/Checkout!")
    cart_str = f"[{status_str}]\n\n" + cart_str

    # Letzte Bestellung mitgeben — fuer "nochmal das gleiche"/"wie immer"
    last_order = session.get("last_order")
    if last_order:
        lo_lines = "\n".join(
            f"  - {i.get('title', '?')} x{i.get('quantity', 1)} [ID:{i.get('product_id', '?')}] à {i.get('price', '?')}€"
            for i in last_order
        )
        cart_str += f"\n\nLETZTE BESTELLUNG des Kunden:\n{lo_lines}"

    if product_context:
        content = f"[PRODUKTDATEN]\n{product_context}\n\n[{cart_str}]\n\n[KUNDE]\n{user_message}"
    else:
        content = f"[{cart_str}]\n\n[KUNDE]\n{user_message}"

    messages.append({"role": "user", "content": content})
    messages = _sanitize_messages(messages)

    try:
        data = _api_call(SYSTEM_PROMPT, messages, tools=ORDER_TOOLS)
        if not data:
            return "Bot ist noch nicht konfiguriert (API Key fehlt). Bitte Admin kontaktieren."

        # Tool-Loop: Claude darf den Katalog selbst durchsuchen.
        # Wall-Clock-Budget verhindert PythonAnywhere-Request-Timeout (502).
        rounds = 0
        loop_start = time.monotonic()
        TOOL_BUDGET_S = 18
        while (data.get("stop_reason") == "tool_use" and rounds < 4
               and (time.monotonic() - loop_start) < TOOL_BUDGET_S):
            rounds += 1
            assistant_content = data.get("content", [])
            tool_results = []
            for block in assistant_content:
                if block.get("type") == "tool_use":
                    log.info(f"Order tool call: {block['name']}({json.dumps(block.get('input', {}), ensure_ascii=False)[:200]})")
                    result = _execute_order_tool(block["name"], block.get("input", {}), session=session)
                    if not _verified:
                        result = _strip_prices(result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": result,
                    })
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})
            # Letzte Runde / Budget fast aus → ohne Tools, damit Claude antworten MUSS
            budget_left = (time.monotonic() - loop_start) < (TOOL_BUDGET_S - 4)
            use_tools = ORDER_TOOLS if (rounds < 4 and budget_left) else None
            data = _api_call(SYSTEM_PROMPT, messages, tools=use_tools)
            if not data:
                return ("Ups, da hakt gerade etwas auf meiner Seite 😬 Versuch's bitte gleich "
                        "nochmal — oder ruf uns kurz an: +49 221 650 878 78, dann helfen wir dir direkt!")

        ai_text = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")

        # Loop endete mit offenem tool_use (Budget/Runden) → finaler Call ohne Tools
        if not ai_text and data.get("stop_reason") == "tool_use":
            assistant_content = data.get("content", [])
            tool_results = [{
                "type": "tool_result", "tool_use_id": b["id"],
                "content": _execute_order_tool(b["name"], b.get("input", {}), session=session),
            } for b in assistant_content if b.get("type") == "tool_use"]
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})
            final = _api_call(SYSTEM_PROMPT, messages, tools=None, max_tokens=1024)
            ai_text = "".join(b["text"] for b in (final or {}).get("content", []) if b.get("type") == "text")

        if not ai_text:
            ai_text = ("Das hat gerade etwas länger gedauert 😅 Sag mir nochmal kurz, "
                       "welches Produkt du genau meinst — dann geht's sofort!")

        # --- Rechnungslink deterministisch behandeln ---
        # Jede vom Modell selbst geschriebene rechnung.dpconnect.de-URL entfernen (sie
        # ist potenziell ein verfälschter Hash). Den exakten Link NICHT hier in den Text
        # tippen — er liegt in session['_pending_invoice'] und wird vom Handler als
        # DOKUMENT (WhatsApp/Telegram) bzw. Link (Webchat) verschickt. Hier nur einen
        # sauberen Einleitungstext sicherstellen.
        inv = session.get("_pending_invoice")
        if _INVOICE_URL_RE.search(ai_text):
            ai_text = _INVOICE_URL_RE.sub("", ai_text)
            ai_text = re.sub(r"[ \t]{2,}", " ", ai_text).strip()
        if inv and inv.get("url") and not ai_text.strip():
            _nr = inv.get("number")
            ai_text = f"Hier ist deine Rechnung{f' zu Bestellung #{_nr}' if _nr else ''} 📄"

        session["conversation"].append({"role": "user", "content": user_message})
        session["conversation"].append({"role": "assistant", "content": ai_text})
        return ai_text

    except Exception as e:
        log.error(f"Claude API Fehler: {e}")
        return ("Ups, da hakt gerade etwas auf meiner Seite 😬 Versuch's bitte gleich nochmal — "
                "oder ruf uns kurz an: +49 221 650 878 78, dann helfen wir dir direkt!")


# ============================================================
# SUPPORT MODE (Tool Use)
# ============================================================

SUPPORT_TOOLS = [
    {
        "name": "lookup_order",
        "description": "Sucht eine Bestellung per Bestellnummer, E-Mail-Adresse oder Telefonnummer. WICHTIG: Der Kunde muss immer seine E-Mail-Adresse zur Verifizierung angeben (verification_email). Ohne Verifizierung werden keine Bestelldetails herausgegeben.",
        "input_schema": {
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "Bestellnummer (z.B. '4521'), E-Mail-Adresse oder Telefonnummer des Kunden"
                },
                "verification_email": {
                    "type": "string",
                    "description": "E-Mail-Adresse des Kunden zur Verifizierung. PFLICHT – frag den Kunden danach bevor du die Bestellung nachschlägst."
                }
            },
            "required": ["identifier", "verification_email"]
        }
    },
    {
        "name": "check_customer_account",
        "description": "Prueft ob ein Kunden-Account mit dieser E-Mail-Adresse existiert. Gibt Account-Status, Name, Erstelldatum und Anzahl der Bestellungen zurueck.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "E-Mail-Adresse des Kunden"
                }
            },
            "required": ["email"]
        }
    },
    {
        "name": "get_order_tracking",
        "description": "Ruft DHL-Tracking-Informationen fuer eine Bestellung ab. WICHTIG: Der Kunde muss seine E-Mail-Adresse zur Verifizierung angeben. Ohne Verifizierung wird kein Tracking herausgegeben.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "Die Bestellnummer"
                },
                "verification_email": {
                    "type": "string",
                    "description": "E-Mail-Adresse des Kunden zur Verifizierung. PFLICHT."
                }
            },
            "required": ["order_id", "verification_email"]
        }
    },
    {
        "name": "get_recent_orders",
        "description": "Zeigt die letzten Bestellungen eines Kunden an. Gibt eine Liste mit Bestellnummer, Status, Datum und Artikelzusammenfassung zurueck.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "E-Mail-Adresse des Kunden"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximale Anzahl der Bestellungen (Standard: 5)",
                    "default": 5
                }
            },
            "required": ["email"]
        }
    },
    {
        "name": "send_new_password",
        "description": "Generiert ein neues Passwort fuer einen registrierten Kunden und sendet es per E-Mail im DP Connect Design. Nutze dieses Tool nur wenn der Kunde ausdruecklich ein neues Passwort per E-Mail erhalten moechte (nicht fuer Login-Link).",
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "E-Mail-Adresse des Kunden"
                }
            },
            "required": ["email"]
        }
    },
    {
        "name": "escalate_to_human",
        "description": "Leitet das Gespraech an einen menschlichen Mitarbeiter weiter. Nutze dieses Tool wenn du das Problem nicht selbst loesen kannst oder der Kunde explizit einen Menschen verlangt.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Kurze Beschreibung warum eskaliert wird"
                },
                "collected_info": {
                    "type": "string",
                    "description": "Alle bisher gesammelten Informationen (Name, E-Mail, Bestellnummer, Problem)"
                }
            },
            "required": ["reason", "collected_info"]
        }
    },
]


def _execute_support_tool(tool_name, tool_input):
    """Execute a support tool and return the result as a dict."""
    from dp_connect_bot.services.woocommerce import wc_client

    try:
        if tool_name == "lookup_order":
            verification_email = tool_input.get("verification_email", "").strip().lower()
            if not verification_email:
                return {"success": False, "error": "Verifizierung erforderlich: Frag den Kunden nach seiner E-Mail-Adresse bevor du Bestellinfos herausgibst."}

            result = wc_client.lookup_order(tool_input["identifier"])
            if result:
                # Fail-CLOSED: E-Mail MUSS vorhanden sein UND passen. Hat die
                # Bestellung keine Rechnungs-E-Mail, kann nicht verifiziert werden
                # → nicht herausgeben (frueher wurde sie dann trotzdem geliefert).
                order_email = result.get("billing", {}).get("email", "").strip().lower()
                if not order_email or verification_email != order_email:
                    log.warning(f"[lookup_order] Email mismatch/fehlt: provided={verification_email}, order={order_email or '(keine)'}")
                    return {"success": False, "error": "Die angegebene E-Mail-Adresse stimmt nicht mit der Bestellung ueberein. Bitte den Kunden bitten, die E-Mail-Adresse zu pruefen, mit der er bei DP Connect registriert ist."}
                return {"success": True, "order": result}
            return {"success": False, "error": "Keine Bestellung gefunden mit dieser Angabe."}

        elif tool_name == "check_customer_account":
            result = wc_client.check_customer(tool_input["email"])
            if result is None:
                return {"success": False, "error": "Konnte den Account gerade nicht pruefen. Bitte spaeter erneut versuchen."}
            return {"success": True, "account": result}

        elif tool_name == "get_order_tracking":
            verification_email = tool_input.get("verification_email", "").strip().lower()
            if not verification_email:
                return {"success": False, "error": "Verifizierung erforderlich: Frag den Kunden nach seiner E-Mail-Adresse bevor du Tracking-Infos herausgibst."}

            # Fail-CLOSED: Tracking nur, wenn die Bestellung gefunden wird UND die
            # E-Mail zur Rechnungsadresse passt. Frueher wurde die Pruefung
            # uebersprungen, wenn lookup_order None lieferte ODER die Bestellung
            # keine Rechnungs-E-Mail hatte → Tracking (DHL-Nr./Link) konnte ohne
            # Verifizierung abfliessen.
            order_data = wc_client.lookup_order(tool_input["order_id"])
            order_email = (order_data or {}).get("billing", {}).get("email", "").strip().lower()
            if not order_data or not order_email or verification_email != order_email:
                log.warning(f"[get_order_tracking] Verifizierung fehlgeschlagen (gefunden={bool(order_data)}, mail_vorhanden={bool(order_email)})")
                return {"success": False, "error": "Die E-Mail-Adresse stimmt nicht mit der Bestellung ueberein (oder die Bestellnummer wurde nicht gefunden). Bitte die bei DP Connect registrierte E-Mail pruefen."}

            result = wc_client.get_order_tracking(tool_input["order_id"])
            if result:
                return {"success": True, "tracking": result}
            return {"success": False, "error": "Keine Tracking-Informationen fuer diese Bestellung gefunden."}

        elif tool_name == "get_recent_orders":
            limit = tool_input.get("limit", 5)
            result = wc_client.get_recent_orders(tool_input["email"], limit=limit)
            if result:
                return {"success": True, "orders": result, "count": len(result)}
            return {"success": True, "orders": [], "count": 0, "message": "Keine Bestellungen mit dieser E-Mail gefunden."}

        elif tool_name == "send_new_password":
            result = wc_client.send_new_password(tool_input["email"])
            return result

        elif tool_name == "escalate_to_human":
            # This is handled specially – we just return success and set a flag
            return {
                "success": True,
                "message": "Eskalation erfolgreich. Ein Mitarbeiter wird sich kuemmern.",
                "reason": tool_input.get("reason", ""),
                "collected_info": tool_input.get("collected_info", ""),
            }

        else:
            return {"success": False, "error": f"Unbekanntes Tool: {tool_name}"}

    except Exception as e:
        log.error(f"Support tool '{tool_name}' error: {e}")
        return {"success": False, "error": "Interner Fehler beim Abrufen der Daten."}


def call_claude_support(session, user_message):
    """Claude mit Tool Use fuer Support-Anfragen.

    Args:
        session: Session dict (with conversation)
        user_message: Nachricht des Kunden

    Returns:
        tuple: (response_text, escalated, escalation_info)
        - response_text: AI response text
        - escalated: bool, True if escalation was triggered
        - escalation_info: dict with reason and collected_info if escalated
    """
    if not ANTHROPIC_API_KEY:
        return "Bot ist noch nicht konfiguriert (API Key fehlt). Bitte Admin kontaktieren.", False, None

    # Build conversation messages (last 20 for support – may need more context)
    messages = list(session["conversation"][-20:])
    messages.append({"role": "user", "content": user_message})
    messages = _sanitize_messages(messages)

    escalated = False
    escalation_info = None
    max_tool_rounds = 5  # Safety limit

    try:
        # First API call with tools
        data = _api_call(SUPPORT_PROMPT, messages, tools=SUPPORT_TOOLS)
        if not data:
            return "Bot ist noch nicht konfiguriert (API Key fehlt). Bitte Admin kontaktieren.", False, None

        rounds = 0
        while data.get("stop_reason") == "tool_use" and rounds < max_tool_rounds:
            rounds += 1

            # Extract tool calls and execute them
            tool_results = []
            assistant_content = data.get("content", [])

            for block in assistant_content:
                if block.get("type") == "tool_use":
                    tool_name = block["name"]
                    tool_input = block["input"]
                    tool_id = block["id"]

                    log.info(f"Support tool call: {tool_name}({json.dumps(tool_input, ensure_ascii=False)})")
                    result = _execute_support_tool(tool_name, tool_input)
                    log.info(f"Support tool result: {tool_name} -> success={result.get('success')}")

                    # Check for escalation
                    if tool_name == "escalate_to_human" and result.get("success"):
                        escalated = True
                        escalation_info = {
                            "reason": result.get("reason", ""),
                            "collected_info": result.get("collected_info", ""),
                        }

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })

            # Next API call with tool results
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})

            data = _api_call(SUPPORT_PROMPT, messages, tools=SUPPORT_TOOLS)
            if not data:
                break

        # Extract final text response
        ai_text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                ai_text += block["text"]

        if not ai_text:
            ai_text = "Da konnte ich gerade nicht richtig drauf zugreifen. Soll ich das an Davides Team weiterleiten?"

        # Save to conversation
        session["conversation"].append({"role": "user", "content": user_message})
        session["conversation"].append({"role": "assistant", "content": ai_text})

        return ai_text, escalated, escalation_info

    except Exception as e:
        log.error(f"Claude Support API Fehler: {e}")
        return "Da ist gerade was schiefgelaufen. Versuch's nochmal oder ruf direkt an: +49 221 650 878 78", False, None
