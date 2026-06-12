"""
WhatsApp adapter – renders BotResponse into WhatsApp Cloud API messages.
"""

import requests

from dp_connect_bot.adapters.base import ChannelAdapter
from dp_connect_bot.config import WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, WHATSAPP_API, log
from dp_connect_bot.models.response import BotResponse, KeyboardType
from dp_connect_bot.services.product_cache import cache
from dp_connect_bot.utils.formatting import format_price_de, get_variant_display_name


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
        if not response.text:
            return

        # Build WhatsApp-specific UI
        buttons = None
        list_menu = None

        for kb in response.keyboards:
            if kb.type == KeyboardType.FLAVORS:
                list_menu = self._build_flavor_list(kb.parent_id)
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
        text = self._clean_text(response.text)
        self._send_message(chat_id, text, buttons=buttons, list_menu=list_menu)

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

    def _send_message(self, phone, text, buttons=None, list_menu=None):
        """Send a message via WhatsApp Cloud API."""
        if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
            log.warning("WhatsApp nicht konfiguriert")
            return False

        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}

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
                self._maybe_enqueue(resp, payload)
                return False
            return True
        except Exception as e:
            log.error(f"WhatsApp send error: {e}")
            from dp_connect_bot.services.send_queue import enqueue
            enqueue(payload)
            return False

    @staticmethod
    def _maybe_enqueue(resp, payload):
        """API-/Auth-Fehler puffern (Meta-Stoerung) — Empfaenger-Fehler nicht."""
        try:
            err = resp.json().get("error", {})
        except Exception:
            err = {}
        # 131026/131030: Empfaenger kein WhatsApp-Nutzer / nicht erlaubt,
        # 100: ungueltige Parameter → Retry zwecklos
        if err.get("code") in (131026, 131030, 100):
            return
        from dp_connect_bot.services.send_queue import enqueue
        enqueue(payload)

    def _build_flavor_list(self, parent_id):
        """Build WhatsApp list menu with flavors (max 10 rows)."""
        variations = cache.get_variations_available(parent_id)
        if not variations:
            return None

        variations = variations[:10]
        rows = []
        for v in variations:
            name = get_variant_display_name(v)
            price = format_price_de(v.get("price"))
            rows.append({
                "id": f"sel_{v['id']}",
                "title": name[:24],
                "description": f"{price}/Stk",
            })

        return {
            "button_text": "Geschmack wählen",
            "sections": [{"title": "Verfügbare Geschmäcker", "rows": rows}],
        }

    def _build_quantity_list(self, kb):
        """Build WhatsApp list menu with quantities (max 10 rows)."""
        try:
            vpe_num = int(kb.vpe)
        except (ValueError, TypeError):
            vpe_num = 1

        product = cache.get_product_by_id(kb.product_id)
        try:
            price_num = float(product.get("price")) if product and product.get("price") else None
        except (ValueError, TypeError):
            price_num = None
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

        rows = []
        for qty in quantities:
            desc = ""
            if price_num and price_num > 0:
                total = price_num * qty
                desc = f"= {format_price_de(total)} netto"
            rows.append({
                "id": f"qty_{kb.product_id}_{qty}",
                "title": f"{qty} Stück",
                "description": desc,
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
