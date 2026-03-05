"""
Telegram adapter – renders BotResponse into Telegram messages with inline keyboards.
"""

import json
import requests

from dp_connect_bot.adapters.base import ChannelAdapter
from dp_connect_bot.config import TELEGRAM_API, log
from dp_connect_bot.models.response import BotResponse, Keyboard, KeyboardType
from dp_connect_bot.services.product_cache import cache
from dp_connect_bot.utils.formatting import format_price_de, get_variant_display_name


FLAVOR_EMOJIS = {
    "watermelon": "🍉", "melon": "🍈", "peach": "🍑", "mango": "🥭",
    "strawberry": "🍓", "cherry": "🍒", "blueberry": "🫐", "raspberry": "🫐",
    "grape": "🍇", "apple": "🍏", "pear": "🍐", "lemon": "🍋", "lime": "🍋",
    "orange": "🍊", "pineapple": "🍍", "banana": "🍌", "kiwi": "🥝",
    "coconut": "🥥", "mint": "🌿", "menthol": "❄️", "ice": "🧊",
    "tobacco": "🍂", "coffee": "☕", "cola": "🫧", "candy": "🍬",
    "berry": "🫐", "tropical": "🌴", "dragon": "🐉", "frozen": "🧊",
    "hazelnut": "🌰", "cream": "🍦", "mojito": "🍸", "lemonade": "🍋",
}


def get_flavor_emoji(name):
    """Findet passendes Emoji fuer einen Geschmacksnamen."""
    name_lower = name.lower()
    for key, emoji in FLAVOR_EMOJIS.items():
        if key in name_lower:
            return emoji
    return "💨"


class TelegramAdapter(ChannelAdapter):
    @property
    def channel_name(self) -> str:
        return "telegram"

    @property
    def chat_id_prefix(self) -> str:
        return "tg_"

    def send_response(self, chat_id, response: BotResponse):
        if response.is_silent:
            return

        # Build Telegram inline keyboards from BotResponse keyboards
        reply_markup = self._build_reply_markup(response.keyboards)

        # Send text
        if response.text:
            self._send_message(chat_id, response.text, reply_markup=reply_markup)

        # Answer callback query if present
        if response.answer_callback_text:
            # This is handled separately in the route
            pass

    def send_typing(self, chat_id):
        try:
            requests.post(
                f"{TELEGRAM_API}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
                timeout=5,
            )
        except Exception:
            pass

    def answer_callback(self, callback_query_id, text=""):
        """Answer a Telegram callback query."""
        try:
            requests.post(
                f"{TELEGRAM_API}/answerCallbackQuery",
                json={"callback_query_id": callback_query_id, "text": text},
                timeout=5,
            )
        except Exception:
            pass

    def _send_message(self, chat_id, text, parse_mode="Markdown", reply_markup=None):
        """Send a message via Telegram API, with chunking and Markdown fallback."""
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for i, chunk in enumerate(chunks):
            payload = {"chat_id": chat_id, "text": chunk, "parse_mode": parse_mode}
            if reply_markup and i == len(chunks) - 1:
                payload["reply_markup"] = (
                    json.dumps(reply_markup) if isinstance(reply_markup, dict) else reply_markup
                )
            try:
                resp = requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)
                if not resp.ok:
                    log.warning(f"Telegram send failed (Markdown): {resp.text}")
                    # Retry without parse_mode (plain text fallback)
                    payload.pop("parse_mode", None)
                    resp2 = requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)
                    if not resp2.ok:
                        log.error(f"Telegram send failed (plain): {resp2.text}")
            except Exception as e:
                log.error(f"Telegram send error: {e}")

    def _build_reply_markup(self, keyboards):
        """Convert list of generic Keyboards to Telegram inline_keyboard format."""
        if not keyboards:
            return None

        all_buttons = []
        for kb in keyboards:
            if kb.type == KeyboardType.FLAVORS:
                markup = self._build_flavor_keyboard(kb)
                if markup:
                    all_buttons.extend(markup.get("inline_keyboard", []))
            elif kb.type == KeyboardType.QUANTITIES:
                markup = self._build_quantity_keyboard(kb)
                if markup:
                    all_buttons.extend(markup.get("inline_keyboard", []))
            elif kb.type == KeyboardType.CALLBACK:
                all_buttons.append([
                    {"text": "📞 Rückruf anfordern", "callback_data": "cb_call"},
                    {"text": "📧 E-Mail schreiben", "callback_data": "cb_email"},
                ])
                all_buttons.append([
                    {"text": "💬 WhatsApp Chat", "callback_data": "cb_whatsapp"},
                ])
            elif kb.type == KeyboardType.MODE_CHOICE:
                all_buttons.append([
                    {"text": "🛒 Bestellen", "callback_data": "mode_order"},
                ])
                all_buttons.append([
                    {"text": "🔑 Login-Probleme", "callback_data": "mode_login"},
                ])
                all_buttons.append([
                    {"text": "📞 Kundenservice", "callback_data": "mode_support"},
                ])
            elif kb.type == KeyboardType.LOGIN_OPTIONS:
                for btn in kb.buttons:
                    all_buttons.append([
                        {"text": btn.text, "callback_data": btn.callback_data}
                    ])
            elif kb.type == KeyboardType.CATEGORIES:
                for btn in kb.buttons:
                    all_buttons.append([
                        {"text": btn.text, "callback_data": btn.callback_data}
                    ])
            elif kb.type == KeyboardType.REORDER_CONFIRM:
                all_buttons.append([
                    {"text": "✅ Ja, gleiche Bestellung", "callback_data": "reorder_yes"},
                    {"text": "❌ Nein, neu bestellen", "callback_data": "reorder_no"},
                ])
            else:
                # Generic buttons
                row = []
                for btn in kb.buttons:
                    row.append({"text": btn.text, "callback_data": btn.callback_data})
                    if len(row) >= 2:
                        all_buttons.append(row)
                        row = []
                if row:
                    all_buttons.append(row)

        if not all_buttons:
            return None
        return {"inline_keyboard": all_buttons}

    def _build_flavor_keyboard(self, kb):
        """Build Telegram flavor inline keyboard."""
        variations = cache.get_variations_available(kb.parent_id)
        if not variations:
            return None

        if len(variations) > 40:
            variations = variations[:40]

        buttons = []
        row = []
        for v in variations:
            name = get_variant_display_name(v)
            emoji = get_flavor_emoji(name)
            label = f"{emoji} {name}"
            if len(label) > 28:
                label = label[:25] + "..."
            callback = f"sel_{v['id']}"
            row.append({"text": label, "callback_data": callback})
            if len(row) >= 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        return {"inline_keyboard": buttons}

    def _build_quantity_keyboard(self, kb):
        """Build Telegram quantity inline keyboard."""
        product = cache.get_product_by_id(kb.product_id)
        try:
            vpe_num = int(kb.vpe)
        except (ValueError, TypeError):
            vpe_num = 1

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

        buttons = []
        row = []
        for qty in quantities:
            if price_num and price_num > 0:
                total = price_num * qty
                if total >= 1000:
                    label = f"{qty} Stk ({total:,.0f}€)".replace(",", ".")
                else:
                    label = f"{qty} Stk ({total:.0f}€)"
            else:
                label = f"{qty} Stk"
            row.append({"text": label, "callback_data": f"qty_{kb.product_id}_{qty}"})
            if len(row) >= 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        if stock_num and stock_num > 0 and stock_num < 100:
            buttons.append([{"text": f"⚠️ Nur noch {stock_num} auf Lager", "callback_data": "noop"}])

        buttons.append([{"text": "✏️ Andere Menge eingeben", "callback_data": f"custom_{kb.product_id}"}])

        return {"inline_keyboard": buttons}
