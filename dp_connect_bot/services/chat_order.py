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
    """Legt die Bestellung an. cart = Session-Cart (product_id, quantity)."""
    from dp_connect_bot.services.product_cache import cache

    items = []
    for i in cart:
        pid = str(i.get("product_id", ""))
        qty = int(i.get("quantity", 1))
        product = cache.get_product_by_id(pid)
        if product and product.get("post_parent"):
            items.append({
                "product_id": int(product["post_parent"]),
                "variation_id": int(pid),
                "quantity": qty,
            })
        else:
            items.append({"product_id": int(pid), "quantity": qty})

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
