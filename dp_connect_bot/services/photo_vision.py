"""
Foto-Verstehen — Kunden schicken Produktfotos ("habt ihr das hier?").

Claude Vision liest die Verpackung (Marke, Aufschrift, Produkttyp, Motive)
und liefert eine kompakte Beschreibung. Daraus baut der Bot eine normale
Text-Anfrage, die durch die bestehende Pipeline laeuft (Suche, Preisschutz,
Warenkorb) — das Foto erbt damit automatisch alle Regeln.
"""

import base64

import requests

from dp_connect_bot.config import ANTHROPIC_API_KEY, CLAUDE_MODEL, log

_MAX_IMAGE_BYTES = 5 * 1024 * 1024

_PHOTO_PROMPT = (
    "Ein B2B-Kunde eines Vape-/Snack-Grosshandels schickt dieses Foto und will "
    "wissen, ob wir das Produkt fuehren. Analysiere die Produktverpackung(en):\n"
    "Format (eine Zeile pro erkanntem Produkt, max 3 Produkte, Deutsch):\n"
    "PRODUKT: Marke=<Markenname falls lesbar>; Aufschrift=<Text auf der Verpackung, "
    "z.B. Produktname/Geschmack/Staerke>; Typ=<Vape/Liquid/Pods/Tabak/Snack/Drink/...>; "
    "Motive=<auffaellige Bildmotive>; Farben=<dominante Farben>\n"
    "Lies den Verpackungstext so genau wie moeglich — Marke und Geschmacksname sind "
    "am wichtigsten. Wenn KEIN Produkt erkennbar ist: 'KEIN_PRODUKT: <was stattdessen "
    "zu sehen ist, 1 Satz>'. Keine Spekulation ueber Preise."
)


def describe_photo(image_bytes: bytes, media_type: str = "image/jpeg") -> str:
    """Beschreibt ein Kundenfoto. Gibt '' bei Fehlern zurueck."""
    if not ANTHROPIC_API_KEY or not image_bytes:
        return ""
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        log.warning(f"Kundenfoto zu gross ({len(image_bytes)} bytes) - uebersprungen")
        return ""
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 300,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.standard_b64encode(image_bytes).decode(),
                        }},
                        {"type": "text", "text": _PHOTO_PROMPT},
                    ],
                }],
            },
            timeout=45,
        )
        resp.raise_for_status()
        data = resp.json()
        desc = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
        log.info(f"Kundenfoto analysiert: {desc[:120]}")
        return desc
    except Exception as e:
        log.error(f"Foto-Analyse fehlgeschlagen: {e}")
        return ""


def build_photo_message(description: str, caption: str = "") -> str:
    """Baut die Text-Nachricht, die fuer das Foto durch die Pipeline laeuft."""
    parts = [f"[KUNDE HAT EIN FOTO GESCHICKT — Bildanalyse:]\n{description}"]
    if caption:
        parts.append(f"[Nachricht des Kunden dazu:] {caption}")
    else:
        parts.append("[Keine Nachricht dazu — der Kunde will vermutlich wissen, ob wir das Produkt haben.]")
    return "\n".join(parts)
