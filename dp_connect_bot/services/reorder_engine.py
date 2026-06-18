"""Vorhersagender Nachbestell-Motor.

Analysiert die Bestell-Historie eines verifizierten Kunden und schaetzt, ob er
fuer eine Nachbestellung "faellig" ist — anhand seines durchschnittlichen
Bestell-RHYTHMUS (Median der Tage zwischen Bestellungen) und der Zeit seit der
letzten Bestellung. Zusaetzlich die haeufigsten Produkte als Auffuell-Vorschlag.

VOLL DEFENSIV: Bei unbekanntem Datums-/Item-Format oder zu wenig Historie wird
einfach NICHTS vorgeschlagen (kein Crash, keine falsche Empfehlung). Daten kommen
ueber chat_order.get_order_history (dp-tools), Besitz ist serverseitig gesichert.
"""

import re
from collections import Counter
from datetime import datetime

from dp_connect_bot.config import log


def _parse_date(s):
    """Robuste Datums-Erkennung (ISO, deutsch, mit/ohne Zeit). None bei Fehlschlag."""
    if not s:
        return None
    s = str(s).strip()
    # ISO zuerst (2026-06-18, 2026-06-18T12:00:00, evtl. mit Zeitzone)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # Deutsch: 18.06.2026 oder 18.06.26
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", s)
    if m:
        try:
            y = int(m.group(3))
            if y < 100:
                y += 2000
            return datetime(y, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _product_key(item_str):
    """Aus einer Item-Zeile ('20x ELF BAR 800 - Cherry') den Produktnamen ziehen
    (Menge + fuehrende Symbole entfernt), klein + getrimmt. '' wenn unklar."""
    t = str(item_str or "").strip()
    # fuehrende Menge "20x " / "20 x " / "20× " entfernen
    t = re.sub(r"^\s*\d+\s*[x×]\s*", "", t, flags=re.IGNORECASE)
    t = t.strip(" -•·\t")
    return t.lower()


def _top_products(orders, limit=5):
    """Haeufigste Produkte ueber alle Bestellungen (best-effort aus den Item-Strings).
    Gibt Liste von Anzeige-Namen zurueck (Original-Schreibweise der letzten Nennung)."""
    counts = Counter()
    display = {}
    for o in orders:
        seen = set()
        for it in (o.get("items") or []):
            key = _product_key(it)
            if not key or key in seen:
                continue
            seen.add(key)
            counts[key] += 1
            # Anzeige ohne fuehrende Menge
            display[key] = re.sub(r"^\s*\d+\s*[x×]\s*", "", str(it).strip(), flags=re.IGNORECASE).strip(" -•·")
    # nur Produkte, die in MEHR als einer Bestellung vorkamen (echte Stammartikel)
    repeat = [(k, c) for k, c in counts.items() if c >= 2]
    repeat.sort(key=lambda x: -x[1])
    top = [display[k] for k, _ in repeat[:limit]]
    return top


def analyze(customer_id):
    """{ok, enough_history, order_count, avg_interval_days, days_since_last, due,
    last_order_date, top_products}. Bei Fehler {ok: False}."""
    try:
        from dp_connect_bot.services.chat_order import get_order_history
        orders = []
        for page in (1, 2, 3):
            res = get_order_history(customer_id, limit=10, page=page)
            if not res.get("ok"):
                break
            batch = res.get("orders", []) or []
            orders.extend(batch)
            if not res.get("has_more") or not batch:
                break

        dated = []
        for o in orders:
            d = _parse_date(o.get("date"))
            if d:
                dated.append((d, o))
        dated.sort(key=lambda x: x[0], reverse=True)  # neueste zuerst

        if len(dated) < 2:
            return {"ok": True, "enough_history": False, "order_count": len(orders)}

        dates = [d for d, _ in dated]
        intervals = sorted(
            iv for iv in ((dates[i] - dates[i + 1]).days for i in range(len(dates) - 1)) if iv > 0
        )
        if not intervals:
            return {"ok": True, "enough_history": False, "order_count": len(orders)}

        avg = intervals[len(intervals) // 2]  # Median (robuster gegen Ausreisser)
        days_since = (datetime.now() - dates[0]).days
        due = days_since >= max(1, int(avg * 0.8))

        return {
            "ok": True,
            "enough_history": True,
            "order_count": len(orders),
            "avg_interval_days": avg,
            "days_since_last": days_since,
            "due": due,
            "last_order_date": dates[0].strftime("%Y-%m-%d"),
            "top_products": _top_products([o for _, o in dated]),
        }
    except Exception as e:
        log.error(f"reorder analyze fehlgeschlagen: {e}")
        return {"ok": False}
