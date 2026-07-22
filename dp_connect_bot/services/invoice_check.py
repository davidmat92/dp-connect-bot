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

# EINDEUTIGE Caption-Hinweise auf einen Rechnungs-/Preisvergleich → direkt in den
# Check, ohne Vorklassifikation. Bewusst ENG (kein bares "vergleich"/"preis" — das
# kaeme auch bei Produktfotos vor); alles Mehrdeutige entscheidet
# looks_like_document() am BILD selbst. (Bei PDFs ist gar kein Trigger noetig.)
_INVOICE_KEYWORDS = (
    "rechnung", "invoice", "preisvergleich", "preis vergleich", "preise vergleichen",
    "günstiger", "guenstiger", "billiger", "konkurrenz", "großhändler",
    "grosshändler", "grosshaendler", "lieferant", "mitbewerber", "sparen", "ersparnis",
)


def is_invoice_request(caption: str) -> bool:
    """True, wenn die Bildunterschrift EINDEUTIG auf einen Rechnungs-/Preisvergleich
    deutet. Mehrdeutige Captions ("Hast du bessere Preise?", "preis?") entscheidet
    stattdessen looks_like_document() am Bild."""
    if not caption:
        return False
    low = caption.lower()
    return any(kw in low for kw in _INVOICE_KEYWORDS)


def looks_like_document(image_bytes: bytes, media_type: str = "image/jpeg") -> bool:
    """Blitz-Klassifikation per Vision (~1-2s, 10 Tokens): Zeigt das Foto ein
    DOKUMENT (Rechnung/Lieferschein/Angebot/Preisliste) statt Produkten/Regal?
    Laeuft fuer JEDES Kundenfoto vor der normalen Erkennung — so werden auch
    Rechnungsfotos OHNE (eindeutige) Caption erkannt ("Hast du bessere Preise?").
    Bei Fehlern/Unklarheit → False = normaler Foto-Flow (sicherer Default)."""
    from dp_connect_bot.config import ANTHROPIC_API_KEY, CLAUDE_MODEL
    if not ANTHROPIC_API_KEY or not image_bytes:
        return False
    try:
        from dp_connect_bot.services.photo_vision import _downscale_for_vision, _MAX_IMAGE_BYTES
        if len(image_bytes) > _MAX_IMAGE_BYTES:
            scaled = _downscale_for_vision(image_bytes)
            if not scaled:
                return False
            image_bytes, media_type = scaled, "image/jpeg"
        import base64 as _b64
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": CLAUDE_MODEL, "max_tokens": 10, "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                 "data": _b64.standard_b64encode(image_bytes).decode()}},
                    {"type": "text", "text": (
                        "Zeigt dieses Foto ein DOKUMENT (Rechnung, Lieferschein, Angebot oder "
                        "Preisliste — also Papier/Bildschirm mit Positionen und Preisen) oder "
                        "etwas ANDERES (Produkte, Verpackungen, ein Regal)? "
                        "Antworte mit GENAU einem Wort: DOKUMENT oder ANDERES.")},
                ],
            }]},
            timeout=25,
        )
        resp.raise_for_status()
        txt = "".join(b.get("text", "") for b in resp.json().get("content", [])
                      if b.get("type") == "text").strip().upper()
        return "DOKUMENT" in txt
    except Exception as e:
        log.error(f"invoice doc-classify fehlgeschlagen: {e}")
        return False


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
    pdf_url = (res.get("pdf_url") or "").strip()

    # Follow-up-Zeilen — Links VERBATIM aus der tools-Antwort (nie durchs
    # Sprachmodell, Tokens/IDs dürfen nicht verändert werden):
    # 1) brandiertes Vergleichs-PDF IMMER anbieten (auch ohne Sparpotenzial),
    # 2) bei Ersparnis: Warenkorb-Link (verifiziert) bzw. Registrierungs-CTA
    #    (unverifiziert — cart_url dort weglassen, ohne Konto bringt er nichts).
    extras = []
    if pdf_url:
        extras.append(f"📄 Dein Vergleich als PDF: {pdf_url}")
    if saving > 0:
        if verified.get("customer_id") and cart_url:
            extras.append(f"🛒 Alle Artikel direkt in den Warenkorb: {cart_url}")
        elif not verified.get("customer_id"):
            extras.append("Diese Preise bekommst du als dpconnect-Kunde — in 2 Minuten "
                          "registriert: https://dpconnect.de/kunde-werden/")
    followup = "\n\n".join(extras)
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
