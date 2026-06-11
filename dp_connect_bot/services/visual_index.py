"""
Visueller Produkt-Index — Claude Vision beschreibt Produktbilder EINMALIG,
die Beschreibungen werden durchsuchbar ("das Liquid mit dem Tier drauf").

Index-Datei: visual_index.json  {product_id: "Motive: ...; Farben: ..."}
Indexierung laeuft in Batches ueber den Admin-Endpoint /admin/visual-index —
zur Laufzeit entstehen KEINE zusaetzlichen Kosten (reine Textsuche).
"""

import json
import os
import threading

import requests

from dp_connect_bot.config import ANTHROPIC_API_KEY, log

INDEX_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "visual_index.json",
)

# Guenstiges Vision-Modell fuer die Einmal-Indexierung
_VISION_MODEL = "claude-haiku-4-5"

_lock = threading.Lock()
_index_cache = None
_index_mtime = None

_DESCRIBE_PROMPT = (
    "Beschreibe NUR was auf dieser Produktverpackung visuell zu sehen ist, "
    "damit Kunden das Produkt per Beschreibung wiederfinden ('das mit dem Tier drauf'). "
    "Format (eine Zeile, Deutsch): "
    "Motive: <generischer Begriff (spezifisch)>, ...; Farben: <2-3 dominante Farben>; Stil: <1-2 Worte>. "
    "Bei Tieren IMMER 'Tier (<Art>)' schreiben, bei Comic-/Cartoonfiguren 'Figur (<was>)', "
    "bei Fruechten 'Frucht (<welche>)', bei Totenkopf/Drache/etc. den Begriff direkt. "
    "Keine Produktnamen, keine Markennamen, kein Text von der Verpackung zitieren. "
    "Wenn nur Text/Logo ohne Motive: 'Motive: keine'. Maximal 30 Worte."
)


def load_index() -> dict:
    global _index_cache, _index_mtime
    with _lock:
        try:
            mtime = os.path.getmtime(INDEX_PATH) if os.path.exists(INDEX_PATH) else None
        except OSError:
            mtime = None
        # Bei Dateiaenderung neu laden (andere Worker-Prozesse indexieren evtl. parallel)
        if _index_cache is not None and mtime == _index_mtime:
            return _index_cache
        try:
            if mtime is not None:
                with open(INDEX_PATH, "r", encoding="utf-8") as fh:
                    _index_cache = json.load(fh)
            else:
                _index_cache = {}
            _index_mtime = mtime
        except Exception as e:
            log.error(f"Visual-Index laden fehlgeschlagen: {e}")
            _index_cache = _index_cache or {}
        return _index_cache


def _save_index(index: dict):
    global _index_cache, _index_mtime
    with _lock:
        tmp = INDEX_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(index, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, INDEX_PATH)
        _index_cache = index
        try:
            _index_mtime = os.path.getmtime(INDEX_PATH)
        except OSError:
            _index_mtime = None


def get_visual(product) -> str:
    """Visuelle Beschreibung fuer ein Produkt (Variationen erben vom Parent)."""
    index = load_index()
    return index.get(str(product.get("id", ""))) or index.get(str(product.get("post_parent", ""))) or ""


def _describe_image(image_url: str) -> str:
    """Ein Bild von Claude Vision beschreiben lassen."""
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": _VISION_MODEL,
            "max_tokens": 150,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "url", "url": image_url}},
                    {"type": "text", "text": _DESCRIBE_PROMPT},
                ],
            }],
        },
        timeout=45,
    )
    resp.raise_for_status()
    data = resp.json()
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()


def index_batch(batch_size: int = 20) -> dict:
    """Indexiert die naechsten N unindexierten Produkte. Gibt Fortschritt zurueck."""
    from dp_connect_bot.services.product_cache import cache, ensure_cache
    ensure_cache()

    index = dict(load_index())
    candidates = [
        p for p in cache.available
        if not p.get("post_parent") and p.get("image_url")
        and str(p["id"]) not in index
    ]

    done, errors = 0, 0
    for p in candidates[:batch_size]:
        try:
            desc = _describe_image(p["image_url"])
            if desc:
                index[str(p["id"])] = desc
                done += 1
                log.info(f"Visual-Index {p['id']}: {desc[:80]}")
        except Exception as e:
            errors += 1
            log.error(f"Visual-Index Fehler bei {p['id']}: {e}")

    if done:
        _save_index(index)

    return {
        "indexed_now": done,
        "errors": errors,
        "total_indexed": len(index),
        "remaining": max(0, len(candidates) - done),
    }
