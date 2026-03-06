"""
Webchat adapter – builds JSON responses for the web chat frontend.
Unlike Telegram/WhatsApp, webchat doesn't push – it returns JSON in the HTTP response.
"""

from dp_connect_bot.adapters.base import ChannelAdapter
from dp_connect_bot.config import log
from dp_connect_bot.models.response import BotResponse, KeyboardType
from dp_connect_bot.services.product_cache import cache
from dp_connect_bot.utils.formatting import format_price_de, get_variant_display_name, stock_label


class WebchatAdapter(ChannelAdapter):
    """Webchat adapter – builds JSON response dicts instead of sending messages."""

    @property
    def channel_name(self) -> str:
        return "web"

    @property
    def chat_id_prefix(self) -> str:
        return "web_"

    def send_response(self, chat_id, response: BotResponse):
        """Not used for webchat – use build_json_response instead."""
        raise NotImplementedError("Webchat uses build_json_response(), not send_response()")

    def send_typing(self, chat_id):
        """Not applicable for webchat (HTTP request/response)."""
        pass

    def build_json_response(self, response: BotResponse):
        """Build JSON dict for the webchat frontend.

        Returns:
            dict with keys: text, keyboards, wc_actions, checkout_url, cart, cart_rich
        """
        result = {
            "text": response.text,
            "keyboards": [],
            "wc_actions": [],
            "checkout_url": response.checkout_url,
            "cart": response.cart,
            "cart_rich": response.cart_rich,
        }

        # Convert keyboards to webchat format
        for kb in response.keyboards:
            result["keyboards"].append(self._keyboard_to_dict(kb))

        # Convert WcAction dataclasses to dicts
        for wca in response.wc_actions:
            result["wc_actions"].append({
                "action": wca.action,
                "product_id": wca.product_id,
                "quantity": wca.quantity,
            })

        return result

    def _keyboard_to_dict(self, kb):
        """Convert a Keyboard to webchat JSON format."""
        if kb.type == KeyboardType.FLAVORS:
            return self._build_flavor_data(kb.parent_id)
        elif kb.type == KeyboardType.QUANTITIES:
            return self._build_quantity_data(kb)
        elif kb.type == KeyboardType.CALLBACK:
            return {
                "type": "callback",
                "buttons": [
                    {"text": "📞 Rückruf anfordern", "callback_data": "cb_call"},
                    {"text": "📧 E-Mail schreiben", "callback_data": "cb_email"},
                    {"text": "💬 WhatsApp Chat", "callback_data": "cb_whatsapp"},
                ],
            }
        elif kb.type == KeyboardType.MODE_CHOICE:
            from dp_connect_bot.services.bot_config import load_bot_config
            buttons = []
            if load_bot_config().get("order_enabled", True):
                buttons.append({"text": "🛒 Bestellen", "callback_data": "mode_order"})
            buttons.append({"text": "🔑 Login-Probleme", "callback_data": "mode_login"})
            buttons.append({"text": "📞 Kundenservice", "callback_data": "mode_support"})
            return {"type": "mode_choice", "buttons": buttons}
        elif kb.type == KeyboardType.LOGIN_OPTIONS:
            return {
                "type": "login_options",
                "buttons": [
                    {"text": btn.text, "callback_data": btn.callback_data}
                    for btn in kb.buttons
                ],
            }
        elif kb.type == KeyboardType.CATEGORIES:
            return {
                "type": "categories",
                "buttons": [
                    {"text": btn.text, "callback_data": btn.callback_data}
                    for btn in kb.buttons
                ],
            }
        elif kb.type == KeyboardType.REORDER_CONFIRM:
            return {
                "type": "reorder_confirm",
                "buttons": [
                    {"text": "✅ Ja, gleiche Bestellung", "callback_data": "reorder_yes"},
                    {"text": "❌ Nein, neu bestellen", "callback_data": "reorder_no"},
                ],
            }
        else:
            return {
                "type": kb.type.value,
                "buttons": [
                    {"text": btn.text, "callback_data": btn.callback_data, "sublabel": btn.sublabel}
                    for btn in kb.buttons
                ],
            }

    def _build_flavor_data(self, parent_id):
        """Build webchat flavor keyboard data with images and prices."""
        variations = cache.get_variations_available(parent_id)
        parent = cache.get_product_by_id(parent_id)

        buttons = []
        for v in variations:
            name = get_variant_display_name(v)
            price = format_price_de(v.get("price"))
            sl = stock_label(v.get("stock"))

            # Bild-URL
            img = v.get("image_url", "")
            if not img and parent:
                img = parent.get("image_url", "")

            buttons.append({
                "text": name,
                "callback_data": f"sel_{v['id']}",
                "sublabel": price,
                "stock_label": sl,
                "image_url": img,
            })

        return {
            "type": "flavors",
            "parent_id": parent_id,
            "title": parent.get("title", "") if parent else "",
            "buttons": buttons,
        }

    def _build_quantity_data(self, kb):
        """Build webchat quantity keyboard data."""
        product = cache.get_product_by_id(kb.product_id)

        try:
            vpe_num = int(kb.vpe)
        except (ValueError, TypeError):
            vpe_num = 1

        try:
            price_num = float(product.get("price")) if product and product.get("price") else None
        except (ValueError, TypeError):
            price_num = None

        multipliers = [1, 2, 3, 5, 10]
        quantities = [vpe_num * m for m in multipliers]

        buttons = []
        for qty in quantities:
            label = f"{qty} Stk"
            sublabel = ""
            if price_num and price_num > 0:
                total = price_num * qty
                sublabel = format_price_de(total)
            buttons.append({
                "text": label,
                "callback_data": f"qty_{kb.product_id}_{qty}",
                "sublabel": sublabel,
            })

        return {
            "type": "quantities",
            "product_id": kb.product_id,
            "label": kb.label,
            "price": kb.price,
            "vpe": kb.vpe,
            "buttons": buttons,
        }
