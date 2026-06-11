"""
Bot-wide runtime configuration (JSON file-based).
Stores global settings (order_enabled) plus per-channel overrides, toggled
from the Bot-System dashboard on tools.dpconnect.de.

Structure:
{
  "order_enabled": true,              # global default (legacy + fallback)
  "channels": {
    "telegram": {"enabled": true, "order_enabled": true, "voice_enabled": true,
                  "disabled_message": "..."},
    "whatsapp": {...},
    "web": {...}
  }
}

Per-channel flags fall back to the global value (order_enabled) or True.
"""

import json
import os
import threading

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..",
    "bot_config.json",
)

_lock = threading.Lock()

CHANNELS = ("telegram", "whatsapp", "web")
CHANNEL_BOOL_FLAGS = ("enabled", "order_enabled", "voice_enabled")

DEFAULT_DISABLED_MESSAGE = (
    "Der Chat ist hier gerade nicht verfügbar. 🙏\n"
    "Du erreichst uns auf dpconnect.de — danke für dein Verständnis!"
)

DEFAULTS = {
    "order_enabled": True,
    "channels": {},
}


def load_bot_config() -> dict:
    """Load the current bot configuration, merged with defaults."""
    with _lock:
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r") as f:
                    stored = json.load(f)
                return {**DEFAULTS, **stored}
            except (json.JSONDecodeError, IOError):
                return dict(DEFAULTS)
        return dict(DEFAULTS)


def save_bot_config(config: dict):
    """Persist the bot configuration to disk."""
    with _lock:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)


def get_channel_config(channel: str) -> dict:
    """Effective flags for one channel (per-channel value > global > True)."""
    cfg = load_bot_config()
    ch = (cfg.get("channels") or {}).get(channel) or {}
    return {
        "enabled": bool(ch.get("enabled", True)),
        "order_enabled": bool(ch.get("order_enabled", cfg.get("order_enabled", True))),
        "voice_enabled": bool(ch.get("voice_enabled", True)),
        "disabled_message": str(ch.get("disabled_message") or "") or DEFAULT_DISABLED_MESSAGE,
    }


def channel_flag(channel: str, flag: str) -> bool:
    """Shortcut: effective boolean flag for a channel."""
    return bool(get_channel_config(channel).get(flag, True))
