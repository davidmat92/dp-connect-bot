"""
Rechnungs-Check — Kunde schickt die Rechnung eines FREMDEN Grosshändlers als
Foto/PDF, das tools-Backend liest sie aus, vergleicht mit unseren Preisen und
liefert einen fertigen Antwort-Text (+ optionalen Warenkorb-Link).

WICHTIG (Architektur): Der komplette Flow ist DETERMINISTISCH — summary_text und
cart_url werden 1:1 aus der tools-Antwort in den Chat gereicht, KEINE KI dazwischen
(kein Umformulieren, kein eigenes Rechnen, und v.a. kein Abtippen des cart_url-Tokens
durchs Sprachmodell — bekannte Token-Verfälschungs-Falle, siehe Rechnungslink-Fix).

Der Call dauert 1-3 Minuten → vorher IMMER die Zwischennachricht senden
(macht run_invoice_check_flow), HTTP-Timeout 300s.
"""

import base64

import requests

from dp_connect_bot.config import TOOLS_API_BASE, TOOLS_VERIFY_TOKEN, log

_MAX_BYTES = 20 * 1024 * 1024   # tools-Limit: 20 MB

# Caption-/Text-Hinweise, dass ein FOTO eine Rechnung zum Preisvergleich ist.
# (Bei PDFs ist KEIN Keyword noetig — ein PDF im B2B-Chat ist praktisch immer
# eine Rechnung; Nicht-Rechnungen beantwortet das tools-Backend ehrlich.)
_INVOICE_KEYWORDS = (
    "rechnung", "invoice", "preisvergleich", "preis vergleich", "preise vergleichen",
    "vergleich", "vergleiche", "vergleichen", "günstiger", "guenstiger", "billiger",
    "konkurrenz", "großhändler", "grosshändler", "grosshaendler", "lieferant",
    "mitbewerber", "sparen", "ersparnis", "was würdet ihr nehmen", "eure preise",
)


def is_invoice_request(caption: str) -> bool:
    """True, wenn die Bildunterschrift auf einen Rechnungs-/Preisvergleich deutet."""
    if not caption:
        return False
    low = caption.lower()
    return any(kw in low for kw in _INVOICE_KEYWORDS)


def check_invoice(file_bytes: bytes, media_type: str, filename: str, customer_ref: str) -> dict:
    """Ruft den tools-Rechnungs-Check auf. Gibt die tools-Antwort zurueck oder
    {ok: False, error: ...}. Dauer 1-3 Minuten — Aufrufer muss vorher eine
    Zwischennachricht senden."""
    if not TOOLS_VERIFY_TOKEN:
        return {"ok": False, "error": "not_configured"}
    if not file_bytes:
        return {"ok": False, "error": "empty"}
    if len(file_bytes) > _MAX_BYTES:
        return {"ok": False, "error": "too_large"}
    try:
        resp = requests.post(
            f"{TOOLS_API_BASE}/api/bot-invoice-check",
            json={
                "image_b64": base64.b64encode(file_bytes).decode(),
                "media_type": media_type or "image/jpeg",
                "filename": filename or "rechnung.jpg",
                "customer_ref": str(customer_ref or ""),
            },
            headers={"X-Bot-Verify-Token": TOOLS_VERIFY_TOKEN,
                     "Content-Type": "application/json"},
            timeout=300,
        )
        if not resp.ok:
            log.error(f"invoice-check HTTP {resp.status_code}: {resp.text[:200]}")
            return {"ok": False, "error": f"http_{resp.status_code}"}
        return resp.json()
    except Exception as e:
        log.error(f"invoice-check fehlgeschlagen: {e}")
        return {"ok": False, "error": str(e)[:200]}


def run_invoice_check_flow(send_text, prefixed_chat_id, file_bytes, media_type, filename):
    """Kompletter Chat-Flow (kanal-agnostisch, send_text = Sender des Adapters):
    Zwischennachricht → tools-Check → summary_text 1:1 → Warenkorb-Link (verifiziert)
    bzw. Registrierungs-CTA (unverifiziert). Schreibt Marker in die Gespraechs-
    Historie, damit die KI bei Folgefragen den Kontext hat."""
    from dp_connect_bot.models.session import session_manager

    session = session_manager.get(prefixed_chat_id)
    verified = (session.get("verified") or {})
    # customer_ref: chat_id (+ Name falls bekannt) → David sieht im Tool, von wem der Check kam
    name = session.get("customer_name") or verified.get("name") or ""
    customer_ref = f"{prefixed_chat_id} ({name})" if name else str(prefixed_chat_id)

    send_text("🧾 Ich schaue mir deine Rechnung an und vergleiche jede Position "
              "mit unseren Preisen — das dauert 1–2 Minuten ⏳")

    res = check_invoice(file_bytes, media_type, filename, customer_ref)

    if not res.get("ok") or not res.get("summary_text"):
        if res.get("error") == "too_large":
            send_text("Die Datei ist leider zu groß (max. 20 MB). 😕 Schick mir gern "
                      "ein Foto in normaler Auflösung oder ein kleineres PDF!")
        else:
            send_text("Das konnte ich leider nicht sauber lesen. 😕 Magst du ein "
                      "schärferes Foto schicken — oder am besten direkt das PDF der Rechnung?")
        session.setdefault("conversation", []).append({
            "role": "user",
            "content": "[Kunde hat eine Fremd-Rechnung zum Preisvergleich geschickt — Auswertung fehlgeschlagen]",
        })
        session_manager.save(prefixed_chat_id, session)
        return

    # summary_text 1:1 — NICHT umformulieren/rechnen (tools liefert das ehrliche Fazit)
    summary = res["summary_text"]
    send_text(summary)

    try:
        saving = float(res.get("saving_total") or 0)
    except (ValueError, TypeError):
        saving = 0.0
    cart_url = (res.get("cart_url") or "").strip()

    followup = ""
    if saving > 0:
        if verified.get("customer_id") and cart_url:
            # Link VERBATIM aus der tools-Antwort — nie durchs Sprachmodell.
            followup = f"Wenn du magst, lege ich dir die Artikel direkt in den Warenkorb: {cart_url}"
        elif not verified.get("customer_id"):
            followup = ("Diese Preise bekommst du als dpconnect-Kunde — die Registrierung "
                        "dauert nur 2 Minuten: https://dpconnect.de/kunde-werden/")
    if followup:
        send_text(followup)

    # Historie: KI kennt bei Folgefragen ("leg mir die in den Warenkorb") den Kontext
    session.setdefault("conversation", []).append({
        "role": "user",
        "content": "[Kunde hat eine Fremd-Rechnung zum Preisvergleich geschickt]",
    })
    session["conversation"].append({
        "role": "assistant",
        "content": summary[:1500] + (f"\n\n{followup}" if followup else ""),
    })
    session_manager.save(prefixed_chat_id, session)
    log.info(f"[invoice-check] {prefixed_chat_id}: matched={res.get('matched')}, "
             f"unclear={res.get('unclear')}, saving={saving}")
