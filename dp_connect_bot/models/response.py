"""
Channel-agnostic response models.
Every handler returns BotResponse, every adapter renders it.
"""

from dataclasses import dataclass, field
from enum import Enum


class KeyboardType(Enum):
    FLAVORS = "flavors"
    QUANTITIES = "quantities"
    CALLBACK = "callback"
    MODE_CHOICE = "mode_choice"
    CATEGORIES = "categories"
    REORDER_CONFIRM = "reorder_confirm"
    LOGIN_OPTIONS = "login_options"


@dataclass
class Button:
    """A channel-agnostic button."""
    text: str                  # Display label
    callback_data: str         # Callback identifier (e.g. "sel_3011", "qty_3011_50")
    sublabel: str = ""         # Secondary text (e.g. price) -- used by webchat


@dataclass
class Keyboard:
    """A channel-agnostic keyboard."""
    type: KeyboardType
    buttons: list[Button] = field(default_factory=list)
    parent_id: str = ""        # For FLAVORS type
    product_id: str = ""       # For QUANTITIES type
    label: str = ""            # Product label for QUANTITIES
    price: str = ""            # Display price for QUANTITIES
    vpe: str = "1"             # VPE for QUANTITIES


@dataclass
class WcAction:
    """A WooCommerce cart sync action for webchat."""
    action: str                # "add", "remove", "clear"
    product_id: str = ""
    quantity: int = 0


@dataclass
class BotResponse:
    """Channel-agnostic response from unified handlers."""
    text: str = ""
    keyboards: list[Keyboard] = field(default_factory=list)
    wc_actions: list[WcAction] = field(default_factory=list)
    checkout_url: str = ""
    cart: list[dict] = field(default_factory=list)
    cart_rich: dict = field(default_factory=dict)
    is_silent: bool = False            # True = don't send anything
    answer_callback_text: str = ""     # For Telegram answerCallbackQuery
