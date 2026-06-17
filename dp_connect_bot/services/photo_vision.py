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

# Regal-Scan: Der Kiosk-/Shop-Inhaber fotografiert sein Verkaufsregal und will
# nachbestellen. Dann zaehlen wir ALLE erkennbaren Produkte auf (nicht nur 3) und
# markieren leere/fast leere Faecher — der Bot bietet danach eine Nachbestellung an.
_SHELF_PROMPT = (
    "Ein B2B-Kunde (Kiosk-/Shop-Inhaber) eines Vape-/Snack-Grosshandels fotografiert "
    "sein eigenes VERKAUFSREGAL und will nachbestellen, was zur Neige geht. "
    "Zaehle JEDES klar erkennbare Produkt auf (so viele wie du sicher lesen kannst, "
    "nicht nur 3). Eine Zeile pro Produkt, Deutsch:\n"
    "PRODUKT: Marke=<Markenname falls lesbar>; Aufschrift=<Produktname/Geschmack/Staerke, "
    "so genau wie lesbar>; Typ=<Vape/Liquid/Pods/Tabak/Snack/Drink/...>; "
    "Bestand=<VOLL|WENIG|LEER — schaetze anhand des Fotos: volles Fach=VOLL, nur noch "
    "1-2 Stueck oder grosse Luecke=WENIG, leeres Fach/Haken=LEER>\n"
    "Wichtig: Lies Marke und Geschmacksname so exakt wie moeglich (entscheidend fuer die "
    "Nachbestellung). Markiere ehrlich, was knapp/leer aussieht — danach koennen wir gezielt "
    "auffuellen. Wenn das Foto KEIN Regal/keine Produkte zeigt: 'KEIN_PRODUKT: <was zu sehen "
    "ist, 1 Satz>'. Keine Preis-Spekulation."
)

# Caption-Hinweise, die einen Regal-/Nachbestell-Scan signalisieren (statt
# einer einzelnen "habt ihr das?"-Frage). Substring-Match auf lowercased caption.
_SHELF_KEYWORDS = (
    "regal", "nachbestell", "nach bestell", "auffüll", "auffuell", "auffüllen",
    "auffuellen", "auffrisch", "was fehlt", "fehlt mir", "fehlt noch", "leer",
    "lager", "vorrat", "bestand", "voll machen", "vollmachen", "scan", "scannen",
    "ganze regal", "ganzes regal", "alles was", "nachbestellung", "nachschub",
)


def _is_shelf_request(caption: str) -> bool:
    """True, wenn die Bildunterschrift auf einen Regal-/Nachbestell-Scan deutet."""
    if not caption:
        return False
    low = caption.lower()
    return any(kw in low for kw in _SHELF_KEYWORDS)


def describe_photo(image_bytes: bytes, media_type: str = "image/jpeg", caption: str = "") -> str:
    """Beschreibt ein Kundenfoto. Gibt '' bei Fehlern zurueck.

    Bei einer Regal-/Nachbestell-Bildunterschrift (siehe `_is_shelf_request`)
    wird der Regal-Scan-Prompt benutzt: ALLE Produkte werden aufgezaehlt und
    leere/knappe Faecher markiert, mit mehr Token-Budget."""
    if not ANTHROPIC_API_KEY or not image_bytes:
        return ""
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        log.warning(f"Kundenfoto zu gross ({len(image_bytes)} bytes) - uebersprungen")
        return ""
    shelf = _is_shelf_request(caption)
    prompt = _SHELF_PROMPT if shelf else _PHOTO_PROMPT
    max_tokens = 900 if shelf else 300
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
                "max_tokens": max_tokens,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.standard_b64encode(image_bytes).decode(),
                        }},
                        {"type": "text", "text": prompt},
                    ],
                }],
            },
            timeout=60 if shelf else 45,
        )
        resp.raise_for_status()
        data = resp.json()
        desc = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
        log.info(f"Kundenfoto analysiert ({'Regal-Scan' if shelf else 'Einzelprodukt'}): {desc[:120]}")
        return desc
    except Exception as e:
        log.error(f"Foto-Analyse fehlgeschlagen: {e}")
        return ""


def build_photo_message(description: str, caption: str = "") -> str:
    """Baut die Text-Nachricht, die fuer das Foto durch die Pipeline laeuft.

    Bei einem Regal-Scan (Nachbestell-Caption) wird die Bildanalyse als
    Auffuell-Vorschlag eingerahmt: der Bot soll die erkannten Produkte im
    Katalog suchen und gezielt eine Nachbestellung anbieten (Fokus auf die
    als WENIG/LEER markierten Faecher), und nach Mengen fragen."""
    if _is_shelf_request(caption):
        parts = [f"[KUNDE HAT EIN REGAL-FOTO GESCHICKT — Nachbestell-Scan, erkannte Produkte:]\n{description}"]
        parts.append(f"[Nachricht des Kunden dazu:] {caption}")
        parts.append(
            "[REGAL-SCAN — so vorgehen: Der Kunde will sein Regal auffuellen. Suche die "
            "erkannten Produkte in unserem Katalog. Priorisiere die als WENIG/LEER "
            "markierten Faecher — die fehlen am dringendsten. Liste die gefundenen, "
            "lieferbaren Produkte kurz auf und frag, welche davon (und in welcher Menge) "
            "ich nachbestellen soll. Produkte, die wir NICHT fuehren, ehrlich weglassen "
            "bzw. kurz als 'haben wir nicht' kennzeichnen — nichts erfinden. Erst auf "
            "Bestaetigung in den Warenkorb legen.]"
        )
        return "\n".join(parts)
    parts = [f"[KUNDE HAT EIN FOTO GESCHICKT — Bildanalyse:]\n{description}"]
    if caption:
        parts.append(f"[Nachricht des Kunden dazu:] {caption}")
    else:
        parts.append("[Keine Nachricht dazu — der Kunde will vermutlich wissen, ob wir das Produkt haben.]")
    return "\n".join(parts)
