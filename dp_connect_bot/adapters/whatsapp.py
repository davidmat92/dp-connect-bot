"""
WhatsApp adapter – renders BotResponse into WhatsApp Cloud API messages.
"""

import requests

from dp_connect_bot.adapters.base import ChannelAdapter
from dp_connect_bot.config import WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, WHATSAPP_API, log
from dp_connect_bot.models.response import BotResponse, KeyboardType
from dp_connect_bot.services.product_cache import cache, staffel_price_for
from dp_connect_bot.utils.formatting import format_price_de, get_variant_display_name, parse_price


class WhatsAppAdapter(ChannelAdapter):
    @property
    def channel_name(self) -> str:
        return "whatsapp"

    @property
    def chat_id_prefix(self) -> str:
        return "wa_"

    def send_response(self, chat_id, response: BotResponse):
        if response.is_silent:
            return
        # Leerer Text OHNE Keyboard/Dokument → nichts zu senden. ABER: ein reines
        # Keyboard (z.B. KI-Antwort war nur [SHOW_FLAVORS:id]) ODER ein reines Dokument
        # (Rechnung) MUSS trotzdem raus. Den fehlenden Body faengt _send_message mit "👇" ab.
        if not response.text and not response.keyboards and not response.document:
            return

        # Build WhatsApp-specific UI
        buttons = None
        list_menu = None
        text_suffix = ""

        for kb in response.keyboards:
            if kb.type == KeyboardType.FLAVORS:
                list_menu, more = self._build_flavor_list(kb.parent_id)
                if more:
                    # WhatsApp-Liste kann max. 10 Zeilen — bei mehr Sorten den
                    # Kunden NICHT im Dunkeln lassen, sondern auf Tippen hinweisen.
                    text_suffix = (f"\n\n💬 Es gibt noch {more} weitere Sorten — "
                                   "tippe einfach den Namen, dann pack ich sie dir ein!")
                break
            elif kb.type == KeyboardType.QUANTITIES:
                list_menu = self._build_quantity_list(kb)
                break
            elif kb.type == KeyboardType.CALLBACK:
                buttons = [
                    {"label": "📞 Rückruf", "callback": "cb_call"},
                    {"label": "📧 E-Mail", "callback": "cb_email"},
                    {"label": "💬 WhatsApp", "callback": "cb_whatsapp"},
                ]
                break
            elif kb.type == KeyboardType.MODE_CHOICE:
                from dp_connect_bot.services.bot_config import channel_flag
                buttons = []
                if channel_flag("whatsapp", "order_enabled"):
                    buttons.append({"label": "🛒 Bestellen", "callback": "mode_order"})
                buttons.append({"label": "🔑 Login", "callback": "mode_login"})
                buttons.append({"label": "📞 Service", "callback": "mode_support"})
                break
            elif kb.type == KeyboardType.CHAT_ORDER:
                buttons = [
                    {"label": btn.text[:20], "callback": btn.callback_data}
                    for btn in kb.buttons[:3]
                ]
                break
            elif kb.type == KeyboardType.LOGIN_OPTIONS:
                buttons = [
                    {"label": btn.text[:20], "callback": btn.callback_data}
                    for btn in kb.buttons[:3]
                ]
                break
            elif kb.type == KeyboardType.REORDER_CONFIRM:
                buttons = [
                    {"label": "✅ Ja, bestellen", "callback": "reorder_yes"},
                    {"label": "❌ Nein", "callback": "reorder_no"},
                ]
                break

        # Clean markdown for WhatsApp (remove unsupported syntax)
        text = self._clean_text(response.text or "") + text_suffix
        self._send_message(chat_id, text, buttons=buttons, list_menu=list_menu)

        # Dokument (z.B. Rechnung) als PDF-Datei nachschicken
        if response.document and response.document.get("url"):
            doc = response.document
            ok = self._send_document(chat_id, doc["url"], doc.get("filename", "Dokument.pdf"))
            if not ok:
                # WhatsApp konnte die Datei nicht laden → Link als Text nachreichen,
                # damit der Kunde die Rechnung trotzdem bekommt.
                self._send_message(chat_id, f"{doc.get('fallback_label', '📄')}:\n{doc['url']}")

    def send_typing(self, chat_id):
        # WhatsApp Cloud API kennt Typing nur als Reaktion auf eine konkrete
        # Nachricht — siehe mark_read_typing(message_id).
        pass

    def mark_read_typing(self, message_id):
        """Markiert die eingehende Nachricht als gelesen + zeigt 'tippt...'
        (max. 25s oder bis zur Antwort)."""
        if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID or not message_id:
            return
        try:
            requests.post(
                f"{WHATSAPP_API}/{WHATSAPP_PHONE_ID}/messages",
                headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}",
                         "Content-Type": "application/json"},
                json={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": message_id,
                    "typing_indicator": {"type": "text"},
                },
                timeout=5,
            )
        except Exception:
            pass

    @staticmethod
    def _split_text(text, limit=4000):
        """Teilt langen Text in Stuecke <= limit, moeglichst an Zeilengrenzen."""
        if len(text) <= limit:
            return [text]
        chunks, rest = [], text
        while len(rest) > limit:
            cut = rest.rfind("\n", 0, limit)
            if cut < limit // 2:
                cut = limit
            chunks.append(rest[:cut].rstrip())
            rest = rest[cut:].lstrip()
        if rest:
            chunks.append(rest)
        return chunks

    def _post_text(self, phone, body):
        """Sendet EINE reine Text-Nachricht (kein Keyboard)."""
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
        payload = {"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": body[:4096]}}
        try:
            resp = requests.post(f"{WHATSAPP_API}/{WHATSAPP_PHONE_ID}/messages",
                                 headers=headers, json=payload, timeout=10)
            if not resp.ok:
                log.error(f"WhatsApp text send error: {resp.text}")
                self._maybe_enqueue(resp, payload)
                return False
            return True
        except Exception as e:
            log.error(f"WhatsApp text send exception: {e}")
            from dp_connect_bot.services.send_queue import enqueue
            enqueue(payload)
            return False

    def _send_message(self, phone, text, buttons=None, list_menu=None):
        """Send a message via WhatsApp Cloud API."""
        # Letzter Meta-Fehlercode dieses Sends (z.B. 131047/470 = 24h-Fenster zu).
        # Aufrufer wie /admin/reply koennen daraus eine KLARE Fehlermeldung bauen.
        # Pro Aufruf-Instanz gespeichert → keine Races (admin_reply nutzt eine eigene
        # frische WhatsAppAdapter-Instanz, nicht die geteilte des Webhooks).
        self._last_error_code = None
        if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
            log.warning("WhatsApp nicht konfiguriert")
            return False

        text = text or ""
        # Interactive (Liste/Buttons) verlangt einen NICHT-leeren Body — sonst lehnt
        # Meta ab UND der Text-Fallback waere auch leer → der Kunde bekaeme GAR nichts
        # (z.B. wenn die KI-Antwort nur ein [SHOW_FLAVORS:id]-Tag war).
        if (list_menu or (buttons and len(buttons) <= 3)) and not text.strip():
            text = "👇"

        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}

        # Lange Nachrichten: WhatsApp KAPPT sonst (interactive-Body 1024 / Text 4096)
        # statt zu splitten → Inhalt (Adresse, Checkout-Hinweis, Bestell-Link) ginge
        # verloren. Daher splitten bzw. langen Teil als separate Text-Nachricht voran.
        interactive = bool(list_menu or (buttons and len(buttons) <= 3))
        if interactive and len(text) > 1024:
            head, _sep, tail = text.rpartition("\n\n")
            if not head or len(tail) > 1024:
                head, tail = text, "👇"
            for chunk in self._split_text(head):
                self._post_text(phone, chunk)
            text = (tail.strip() or "👇")[:1024]
        elif not interactive and len(text) > 4096:
            ok = True
            for chunk in self._split_text(text):
                ok = self._post_text(phone, chunk) and ok
            return ok

        if list_menu:
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "interactive",
                "interactive": {
                    "type": "list",
                    "body": {"text": text[:1024]},
                    "action": {
                        "button": list_menu.get("button_text", "Auswählen")[:20],
                        "sections": list_menu["sections"],
                    },
                },
            }
        elif buttons and len(buttons) <= 3:
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": text[:1024]},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": b["callback"], "title": b["label"][:20]}}
                            for b in buttons[:3]
                        ],
                    },
                },
            }
        else:
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": text[:4096]},
            }

        try:
            resp = requests.post(
                f"{WHATSAPP_API}/{WHATSAPP_PHONE_ID}/messages",
                headers=headers,
                json=payload,
                timeout=10,
            )
            if not resp.ok:
                log.error(f"WhatsApp send error: {resp.text}")
                try:
                    self._last_error_code = resp.json().get("error", {}).get("code")
                except Exception:
                    self._last_error_code = None
                # Interaktive Nachricht von Meta abgelehnt (z.B. Param-Fehler 100:
                # Titel/Body zu lang, zu viele Rows) → als REINEN TEXT nachliefern,
                # damit der Kunde wenigstens die Antwort bekommt statt nichts.
                if payload.get("type") == "interactive":
                    err_code = self._last_error_code
                    if err_code == 100 or resp.status_code == 400:
                        try:
                            r2 = requests.post(
                                f"{WHATSAPP_API}/{WHATSAPP_PHONE_ID}/messages",
                                headers=headers,
                                json={"messaging_product": "whatsapp", "to": phone,
                                      "type": "text", "text": {"body": text[:4096] or "👇"}},
                                timeout=10,
                            )
                            if r2.ok:
                                log.info("WhatsApp: interaktiv abgelehnt → als Text nachgeliefert")
                                return True
                            log.error(f"WhatsApp text-fallback error: {r2.text}")
                        except Exception as e2:
                            log.error(f"WhatsApp text-fallback exception: {e2}")
                self._maybe_enqueue(resp, payload)
                return False
            return True
        except Exception as e:
            log.error(f"WhatsApp send error: {e}")
            from dp_connect_bot.services.send_queue import enqueue
            enqueue(payload)
            return False

    def _send_document(self, phone, url, filename="Dokument.pdf", caption=""):
        """Sendet ein Dokument (z.B. Rechnungs-PDF) per Link — WhatsApp laedt die
        Datei selbst von der URL und stellt sie als Datei zu. True/False."""
        if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID or not url:
            return False
        document = {"link": url, "filename": (filename or "Dokument.pdf")[:240]}
        if caption:
            document["caption"] = caption[:1024]
        payload = {"messaging_product": "whatsapp", "to": phone, "type": "document", "document": document}
        try:
            resp = requests.post(
                f"{WHATSAPP_API}/{WHATSAPP_PHONE_ID}/messages",
                headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"},
                json=payload, timeout=20,
            )
            if not resp.ok:
                log.error(f"WhatsApp document send error: {resp.text}")
                return False
            return True
        except Exception as e:
            log.error(f"WhatsApp document send exception: {e}")
            return False

    def send_template(self, phone, template_name, body_params=None, lang="de"):
        """Sendet eine genehmigte WhatsApp-Vorlage (fuer proaktive Nachrichten
        AUSSERHALB des 24h-Fensters, z.B. Restock-Alarm). body_params = Liste der
        {{1}},{{2}},…-Platzhalter-Werte im Template-Body."""
        if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
            log.warning("WhatsApp nicht konfiguriert (Template)")
            return False
        components = []
        if body_params:
            components.append({
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in body_params],
            })
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": lang},
                "components": components,
            },
        }
        try:
            resp = requests.post(
                f"{WHATSAPP_API}/{WHATSAPP_PHONE_ID}/messages",
                headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"},
                json=payload, timeout=10,
            )
            if not resp.ok:
                log.error(f"WhatsApp template send error: {resp.text}")
            return resp.ok
        except Exception as e:
            log.error(f"WhatsApp template send exception: {e}")
            return False

    @staticmethod
    def _maybe_enqueue(resp, payload):
        """API-/Auth-Fehler puffern (Meta-Stoerung) — PERMANENTE Fehler nicht
        (sonst vergiftet z.B. eine Out-of-24h-Window-Nachricht die Queue)."""
        from dp_connect_bot.services.send_queue import enqueue, PERMANENT_SEND_ERRORS
        try:
            err = resp.json().get("error", {})
        except Exception:
            err = {}
        code = err.get("code")
        if code in PERMANENT_SEND_ERRORS:
            if code in (131047, 470):
                log.warning(f"WhatsApp: Empfaenger ausserhalb 24h-Fenster (Code {code}) — "
                            "Freitext nicht zustellbar, nicht gepuffert (braucht Template).")
            return
        enqueue(payload)

    def _build_flavor_list(self, parent_id):
        """Build WhatsApp list menu with flavors (max 10 rows).

        Returns (menu, more_count): more_count = wie viele Sorten NICHT in die
        Liste passten (WhatsApp-Limit 10), damit der Aufrufer darauf hinweisen kann.
        """
        variations = cache.get_variations_available(parent_id)
        if not variations:
            return None, 0

        total = len(variations)
        # Bei >10 Sorten passt nicht alles in die Meta-Liste (max 10 Zeilen). Statt
        # 10 Sorten stumm abzuschneiden: 9 Sorten + eine SICHTBARE "weitere"-Zeile,
        # damit der Kunde IM Listen-Dialog sieht, dass es mehr gibt + tippen kann.
        shown = variations[:9] if total > 10 else variations[:10]
        more = total - len(shown)
        rows = []
        for v in shown:
            name = get_variant_display_name(v)
            price = format_price_de(v.get("price"))
            # Row-Titel max 24 Zeichen. Wird der Name gekuerzt, kann das
            # unterscheidende Suffix (z.B. Nikotinstaerke "(20 mg)") verloren
            # gehen → zwei Varianten saehen identisch aus. Dann den VOLLEN Namen
            # in die Description (72 Zeichen Platz) ziehen, damit sie unterscheidbar bleiben.
            desc = f"{name} · {price}/Stk"[:72] if len(name) > 24 else f"{price}/Stk"[:72]
            rows.append({
                "id": f"sel_{v['id']}",
                # Leerer Row-Titel → Meta lehnt die GANZE Liste ab (400) und der
                # Kunde saehe die Auswahl nicht. Fallback erzwingt einen Titel.
                "title": (name or "Variante")[:24],
                "description": desc,
            })

        # Sichtbare "weitere Sorten"-Zeile (statt stummer Abschnitt) → fuehrt zum
        # Tippen, deckt ALLE restlichen Sorten ab.
        if more > 0:
            rows.append({
                "id": f"flavmore_{parent_id}",
                "title": f"➕ {more} weitere Sorten"[:24],
                "description": "Hier tippen → dann Namen schreiben"[:72],
            })
        section_title = (f"{len(shown)} von {total} Sorten" if more > 0 else "Verfügbare Geschmäcker")
        return {
            "button_text": "Geschmack wählen",
            "sections": [{"title": section_title[:24], "rows": rows}],
        }, more

    def _build_quantity_list(self, kb):
        """Build WhatsApp list menu with quantities (max 10 rows)."""
        try:
            vpe_num = int(kb.vpe)
        except (ValueError, TypeError):
            vpe_num = 1

        product = cache.get_product_by_id(kb.product_id)
        # parse_price ist robust gegen "5,30€"/Komma-Formate (bares float() wuerfe);
        # liefert 0.0 bei fehlend/ungueltig → die Preiszeile wird dann weggelassen.
        price_num = parse_price(product.get("price")) if product else 0.0
        try:
            stock_num = int(product.get("stock")) if product and product.get("stock") else None
        except (ValueError, TypeError):
            stock_num = None

        multipliers = [1, 2, 3, 5, 10, 20, 50]
        quantities = [vpe_num * i for i in multipliers if vpe_num * i <= 1000]
        if not quantities:
            quantities = [10, 20, 50, 100, 200]

        if stock_num and stock_num > 0:
            quantities = [q for q in quantities if q <= stock_num]
            if not quantities and vpe_num <= stock_num:
                quantities = [vpe_num]

        quantities = quantities[:10]

        # Keine gueltige Menge (z.B. Restbestand < VPE/Mindestbestellung) → KEINE
        # leere Liste bauen: eine interactive-Liste mit 0 Zeilen lehnt Meta mit 400
        # ab. None → der Kunde bekommt die "Wie viele?"-Textfrage und kann tippen;
        # der Add-Pfad meldet dann sauber "nur noch X, Mindestbestellung Y".
        if not quantities:
            return None

        rows = []
        for qty in quantities:
            desc = ""
            # Staffelpreis der erreichten Stufe verwenden — sonst zeigt die Zeile
            # einen ZU HOHEN Gesamtpreis (Basispreis × Menge), obwohl der Warenkorb
            # den Rabatt anwendet. Sichtbarer Rabatt = Kaufanreiz fuer mehr Menge.
            sp = staffel_price_for(product, qty)
            unit = sp if sp is not None else price_num
            if unit and unit > 0:
                total = unit * qty
                desc = f"= {format_price_de(total)} netto"
                if sp is not None and price_num and sp < price_num:
                    desc += f" 💥 nur {format_price_de(sp)}/Stk"
            rows.append({
                "id": f"qty_{kb.product_id}_{qty}",
                "title": f"{qty} Stück"[:24],
                "description": desc[:72],
            })

        return {
            "button_text": "Menge wählen",
            "sections": [{"title": "Menge auswählen", "rows": rows}],
        }

    @staticmethod
    def _clean_text(text):
        """Claude-Markdown → WhatsApp-Format (**fett** → *fett* etc.)."""
        from dp_connect_bot.utils.formatting import markdown_to_chat
        return markdown_to_chat(text)
