"""
Formatting helpers – pure functions, no external dependencies.
"""

import re


def markdown_to_chat(text):
    """Claude-Markdown → Chat-Format fuer WhatsApp/Telegram (Markdown V1).

    **fett** wird dort NICHT gerendert — Kunden sehen Sternchen-Salat.
    Konvertiert zu *fett*, Header zu Fettzeilen, kollabiert Leerzeilen.
    """
    if not text:
        return text
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"^#{1,5}\s*(.+)$", r"*\1*", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def kebab_to_readable(text):
    """Konvertiert 'strawberry-ice-cream' zu 'Strawberry Ice Cream'."""
    if not text:
        return ""
    return text.replace("-", " ").title()


def pipe_to_list(text):
    """Konvertiert 'Berry Kush|Black Amnesia' zu ['Berry Kush', 'Black Amnesia']."""
    if not text:
        return []
    return [s.strip() for s in str(text).split("|") if s.strip()]


def get_variant_display_name(product):
    """Ermittelt den besten Anzeigenamen fuer eine Variante."""
    name = ""
    for field in ["geschmack", "sorte", "farbe", "auswahl"]:
        val = product.get(field)
        if val:
            readable = kebab_to_readable(val)
            name = re.sub(r'\s*\d+[gG]$', '', readable).strip()
            break
    if not name:
        name = product.get("flavor") or product.get("title", "Unbekannt")
    # Nikotingehalt anhaengen, sonst sind z.B. 10mg- und 20mg-Variante
    # desselben Geschmacks nicht unterscheidbar (ELFLIQ etc.)
    niko = str(product.get("nikotingehalt") or "").strip()
    if niko and niko.lower() not in name.lower():
        niko_short = re.sub(r'\s*/?\s*ml\s*nikotinsalz$', '', kebab_to_readable(niko), flags=re.IGNORECASE).strip()
        niko_short = re.sub(r'\bMg\b', 'mg', niko_short)
        name = f"{name} ({niko_short})"
    return name


def get_variant_type_label(product):
    """Gibt das Label fuer den Varianten-Typ zurueck (Geschmack, Sorte, Farbe, Auswahl)."""
    if product.get("geschmack"):
        return "Geschmack"
    if product.get("sorte"):
        return "Sorte"
    if product.get("farbe"):
        return "Farbe"
    if product.get("auswahl"):
        return "Auswahl"
    return "Variante"


def format_price_de(price):
    """Formatiert Preis deutsch: 4.50 -> 4,50€."""
    try:
        p = float(price)
        return f"{p:.2f}".replace(".", ",") + "€"
    except (ValueError, TypeError):
        return ""


def parse_price(price_str):
    """Parst Preis-Strings: '5,30€', '5.30', '5,30', '5.30€' -> 5.3"""
    if not price_str:
        return 0.0
    try:
        cleaned = str(price_str).replace("€", "").replace(" ", "").strip()
        if "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        elif "," in cleaned:
            cleaned = cleaned.replace(",", ".")
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def stock_label(stock):
    """Wandelt exakten Lagerbestand in Kategorie um. NIE genaue Zahlen an Claude."""
    try:
        s = int(stock)
    except (ValueError, TypeError):
        return ""
    if s > 300:
        return "Vorrätig"
    elif s >= 50:
        return "Begrenzt verfügbar"
    elif s >= 1:
        return "Fast ausverkauft"
    else:
        return "Ausverkauft"
