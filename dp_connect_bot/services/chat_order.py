"""
Chat-Direktbestellung — verifizierte Kunden schliessen die Bestellung
direkt im Chat ab, ohne Website. Anlage laeuft ueber tools.dpconnect.de.

Zahlarten: "Kauf auf Rechnung" nur wenn in der Kundenverwaltung
(tools.dpconnect.de → Kunden → "Kauf auf Rechnung erlauben") freigeschaltet,
sonst Vorkasse. Feature-Toggle: Bot-System → Einstellungen → Chat-Bestellung.
"""

import requests

from dp_connect_bot.config import TOOLS_API_BASE, TOOLS_VERIFY_TOKEN, log


def _post(path: str, payload: dict) -> dict:
    resp = requests.post(
        f"{TOOLS_API_BASE}{path}",
        json=payload,
        headers={"X-Bot-Verify-Token": TOOLS_VERIFY_TOKEN, "Content-Type": "application/json"},
        timeout=30,
    )
    if resp.status_code == 403:
        return {"ok": False, "forbidden": True}
    resp.raise_for_status()
    return resp.json()


def get_customer_order_info(customer_id) -> dict:
    """{rechnung_erlaubt, address_text, has_address} oder {error}."""
    try:
        return _post("/api/bot-order/customer-info", {"customer_id": int(customer_id)})
    except Exception as e:
        log.error(f"chat_order customer-info fehlgeschlagen: {e}")
        return {"ok": False, "error": True}


def create_order(customer_id, cart, payment_method: str, channel: str) -> dict:
    """Legt die Bestellung an. cart = Session-Cart (product_id, quantity).

    WICHTIG: Pro Position wird der STUECKPREIS mitgesendet (Staffelpreis fuer die
    Menge, sonst der im Warenkorb gezeigte Preis). Sonst wuerde WooCommerce bei
    einer REST-Bestellung den Normalpreis ansetzen und den Staffelrabatt ignorieren
    — der Kunde wuerde teurer berechnet als im Chat zugesagt.
    """
    from dp_connect_bot.services.product_cache import cache, staffel_price_for

    items = []
    for i in cart:
        pid = str(i.get("product_id", ""))
        qty = int(i.get("quantity", 1))
        product = cache.get_product_by_id(pid)
        # Stueckpreis bestimmen: Staffelpreis (autoritativ) ODER der gezeigte Preis
        unit = staffel_price_for(product, qty) if product else None
        if unit is None:
            try:
                unit = float(str(i.get("price", "")).replace(",", "."))
            except (ValueError, TypeError):
                unit = None
        if product and product.get("post_parent"):
            entry = {
                "product_id": int(product["post_parent"]),
                "variation_id": int(pid),
                "quantity": qty,
            }
        else:
            entry = {"product_id": int(pid), "quantity": qty}
        if unit and unit > 0:
            entry["price"] = round(unit, 2)
        items.append(entry)

    try:
        return _post("/api/bot-order/create", {
            "customer_id": int(customer_id),
            "items": items,
            "payment_method": payment_method,
            "channel": channel,
        })
    except Exception as e:
        log.error(f"chat_order create fehlgeschlagen: {e}")
        return {"ok": False, "error": True}


def get_order_history(customer_id, limit=5, page=1) -> dict:
    """Bestellungen des Kunden (paginiert). {ok, orders, page, has_more} oder {error}."""
    try:
        return _post("/api/bot-order/history", {
            "customer_id": int(customer_id), "limit": int(limit), "page": int(page),
        })
    except Exception as e:
        log.error(f"chat_order history fehlgeschlagen: {e}")
        return {"ok": False, "error": True}


def get_order_detail(customer_id, order_id) -> dict:
    """Volle Details einer Bestellung. {ok, ...} oder {ok:False, reason}."""
    try:
        return _post("/api/bot-order/detail", {
            "customer_id": int(customer_id), "order_id": int(order_id),
        })
    except Exception as e:
        log.error(f"chat_order detail fehlgeschlagen: {e}")
        return {"ok": False, "error": True}


def get_invoice_link(customer_id, order_id=None) -> dict:
    """Share-Link zur Rechnung. {ok, url, number} oder {ok:False, reason}."""
    payload = {"customer_id": int(customer_id)}
    if order_id:
        payload["order_id"] = int(order_id)
    try:
        return _post("/api/bot-order/invoice-link", payload)
    except Exception as e:
        log.error(f"chat_order invoice-link fehlgeschlagen: {e}")
        return {"ok": False, "error": True}


def get_invoices(customer_id, only_open=True) -> dict:
    """Rechnungen mit Zahlstatus. {ok, invoices, total_open, ...} oder {error}."""
    try:
        return _post("/api/bot-order/invoices", {
            "customer_id": int(customer_id), "only_open": bool(only_open),
        })
    except Exception as e:
        log.error(f"chat_order invoices fehlgeschlagen: {e}")
        return {"ok": False, "error": True}


def get_tracking(customer_id, order_id=None) -> dict:
    """Sendungsverfolgung + Status einer Bestellung. {ok, status, tracking, ...}."""
    payload = {"customer_id": int(customer_id)}
    if order_id:
        payload["order_id"] = int(order_id)
    try:
        return _post("/api/bot-order/tracking", payload)
    except Exception as e:
        log.error(f"chat_order tracking fehlgeschlagen: {e}")
        return {"ok": False, "error": True}
