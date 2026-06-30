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
    "wissen, ob wir das/die Produkt(e) fuehren. Analysiere die Produktverpackung(en):\n"
    "Format (eine Zeile pro erkanntem Produkt, bis zu 5 Produkte, Deutsch):\n"
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


# Order-Foto: Der Kunde fotografiert eine BESTELLUNG/einen Warenstapel und will
# "genau so eine Bestellung". Dann ALLE Produkte erfassen UND die Menge je Produkt
# schaetzen (Kartons/Stapel zaehlen) — und als konkreten Bestell-Vorschlag anbieten.
_ORDER_PROMPT = (
    "Ein B2B-Kunde (Kiosk/Shop) eines Vape-/Snack-Grosshandels schickt dieses Foto "
    "und will GENAU SO eine Bestellung aufgeben ('ich brauche so eine Bestellung'). "
    "Erfasse das Foto SO VOLLSTAENDIG WIE MOEGLICH: Zaehle JEDES erkennbare Produkt auf "
    "(so viele wie du sicher lesen kannst — AUF KEINEN FALL nur 3) und SCHAETZE die "
    "Menge je Produkt anhand der sichtbaren Packungen/Stangen/Kartons/Stapel. "
    "Eine Zeile pro Produkt, Deutsch:\n"
    "PRODUKT: Marke=<Markenname>; Aufschrift=<Produktname/Geschmack/Staerke, so genau wie "
    "lesbar>; Typ=<Vape/Liquid/Pods/Tabak/Snack/Drink/...>; Menge=<geschaetzte Stueckzahl, "
    "z.B. 20; bei Unsicherheit grob schaetzen und mit ~ markieren>\n"
    "Wichtig: Marke + Geschmack + Staerke (z.B. mg, sichtbar auf Karton/Packung) so exakt "
    "wie moeglich lesen — entscheidend fuer die Bestellung. Zaehle die sichtbaren "
    "Einheiten/Kartons mit, um die Menge zu schaetzen. Lieber EIN Produkt mehr aufzaehlen "
    "als eins zu uebersehen. Wenn KEINE Produkte erkennbar: 'KEIN_PRODUKT: <was zu sehen "
    "ist, 1 Satz>'. Keine Preis-Spekulation."
)

# Caption-Hinweise "ich will GENAU SO eine Bestellung" (nicht nur 'habt ihr das?').
# "nachbestellen" liegt bewusst NICHT hier — das faengt schon der Regal-Scan ab.
_ORDER_KEYWORDS = (
    "so eine bestellung", "solche bestellung", "diese bestellung", "so eine bestllung",
    "so was", "sowas", "so etwas", "das gleiche", "das selbe", "dasselbe", "genau das",
    "genauso", "genau so", "brauche das", "brauche so", "bräuchte", "braeuchte",
    "hätte gern", "haette gern", "möchte das", "moechte das", "will das", "das nochmal",
    "nochmal das", "alles davon", "wie hier", "wie auf dem bild", "bestellen", "bestellung",
)


def _is_order_request(caption: str) -> bool:
    """True, wenn der Kunde GENAU SO eine Bestellung will → ALLE Produkte + geschaetzte
    Mengen erfassen (statt nur 'habt ihr das?'). Regal-Scan hat Vorrang."""
    if not caption or _is_shelf_request(caption):
        return False
    low = caption.lower()
    return any(kw in low for kw in _ORDER_KEYWORDS)


def _downscale_for_vision(image_bytes: bytes):
    """Verkleinert ein zu grosses Bild auf <= _MAX_IMAGE_BYTES (max ~1568px lange
    Kante, JPEG). Gibt die neuen Bytes oder None (Pillow fehlt / Bild kaputt).
    Pillow-Import bewusst LAZY + try/except — ist es im PA-venv nicht vorhanden,
    soll nicht das ganze Foto-Modul brechen, sondern nur sauber degradieren."""
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((1568, 1568))
        for quality in (85, 70, 55, 40):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= _MAX_IMAGE_BYTES:
                return data
        return None
    except Exception as e:
        log.error(f"Bild-Verkleinerung fehlgeschlagen: {e}")
        return None


def describe_photo(image_bytes: bytes, media_type: str = "image/jpeg", caption: str = "") -> str:
    """Beschreibt ein Kundenfoto. Gibt '' bei Fehlern zurueck.

    Bei einer Regal-/Nachbestell-Bildunterschrift (siehe `_is_shelf_request`)
    wird der Regal-Scan-Prompt benutzt: ALLE Produkte werden aufgezaehlt und
    leere/knappe Faecher markiert, mit mehr Token-Budget."""
    if not ANTHROPIC_API_KEY or not image_bytes:
        return ""
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        # Statt grosse Fotos abzulehnen: herunterskalieren (Claude Vision will eh
        # max ~1568px) → das Foto FUNKTIONIERT, statt den Kunden mit einer Fehler-
        # meldung abzuweisen. Klappt das nicht (Pillow fehlt/Bild kaputt) → aufgeben.
        scaled = _downscale_for_vision(image_bytes)
        if not scaled:
            log.warning(f"Kundenfoto zu gross ({len(image_bytes)} bytes) + nicht verkleinerbar - uebersprungen")
            return ""
        image_bytes, media_type = scaled, "image/jpeg"
        log.info(f"Kundenfoto auf {len(image_bytes)} bytes verkleinert (war zu gross)")
    shelf = _is_shelf_request(caption)
    order = _is_order_request(caption)  # hat shelf-Vorrang schon berücksichtigt
    # Regal-Scan UND Order-Foto brauchen viel Budget (viele Produkte + Mengen);
    # das einfache "habt ihr das?" bleibt knapp.
    if shelf:
        prompt, max_tokens, mode = _SHELF_PROMPT, 900, "Regal-Scan"
    elif order:
        prompt, max_tokens, mode = _ORDER_PROMPT, 1000, "Order-Foto"
    else:
        prompt, max_tokens, mode = _PHOTO_PROMPT, 300, "Einzelprodukt"
    thorough = shelf or order
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
            timeout=60 if thorough else 45,
        )
        resp.raise_for_status()
        data = resp.json()
        desc = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
        log.info(f"Kundenfoto analysiert ({mode}): {desc[:120]}")
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
    if _is_order_request(caption):
        parts = [f"[KUNDE WILL GENAU DIESE BESTELLUNG — Foto-Analyse mit geschaetzten Mengen:]\n{description}"]
        parts.append(f"[Nachricht des Kunden dazu:] {caption}")
        parts.append(
            "[ORDER-FOTO — so vorgehen: Der Kunde moechte GENAU diese Bestellung aufgeben. "
            "Suche ALLE erkannten Produkte im Katalog (search_products / get_product_variants) "
            "und nimm die geschaetzten Mengen als KONKRETEN Vorschlag. Liste auf, was du gefunden "
            "hast — je Produkt MIT der geschaetzten Menge — und frag nur noch kurz zur "
            "BESTAETIGUNG ('Passt das so, oder soll ich eine Menge anpassen?') statt offen "
            "'wie viele willst du?'. Sei vollstaendig: lieber ein Produkt mehr aufgreifen. Bei "
            "Liquids/Pods die Staerke (z.B. mg) gegen die Varianten abgleichen (mehrere passende "
            "Varianten gebuendelt fragen). Produkte, die wir NICHT fuehren, ehrlich kennzeichnen "
            "statt zu erfinden. Erst auf Bestaetigung in den Warenkorb. Sei verkaufsfoerdernd und "
            "biete an, ALLES auf einmal einzupacken.]"
        )
        return "\n".join(parts)
    parts = [f"[KUNDE HAT EIN FOTO GESCHICKT — Bildanalyse:]\n{description}"]
    if caption:
        parts.append(f"[Nachricht des Kunden dazu:] {caption}")
    else:
        parts.append("[Keine Nachricht dazu — der Kunde will vermutlich wissen, ob wir das Produkt haben.]")
    return "\n".join(parts)
