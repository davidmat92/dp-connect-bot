"""Foto-Inventur — speichert die Ergebnisse der Regal-Scans pro Kunde.

So kann der Bot beim Nachbestellen an das LETZTE Regal-Foto erinnern ("bei deinem
letzten Regal-Foto vor 5 Tagen waren X leer, Y knapp"), auch ohne dass der Kunde
gerade ein neues Foto schickt. Erste Schicht der Foto-Inventur; Zeitreihe/Verbrauchs-
Prognose ist der naechste Ausbau.

Gespeichert wird je chat_id der ZULETZTE Scan (Produkt + Bestand VOLL/WENIG/LEER) mit
Zeitstempel. Voll defensiv — Parsing-/DB-Fehler werfen nie.
"""

import json
import re
import sqlite3
import time
from threading import Lock

from dp_connect_bot.config import HISTORY_DB_PATH, log

_lock = Lock()
_initialized = False


def _ensure(c):
    global _initialized
    if not _initialized:
        c.execute("CREATE TABLE IF NOT EXISTS shelf_scans ("
                  "chat_id TEXT PRIMARY KEY, ts REAL, items TEXT)")
        _initialized = True


def _parse(description):
    """Zieht Produkte + Bestand aus der Regal-Scan-Beschreibung
    ('PRODUKT: Marke=...; Aufschrift=...; Bestand=LEER')."""
    items = []
    for line in (description or "").splitlines():
        if "produkt:" not in line.lower():
            continue
        marke = re.search(r"Marke=([^;]+)", line, re.IGNORECASE)
        auf = re.search(r"Aufschrift=([^;]+)", line, re.IGNORECASE)
        best = re.search(r"Bestand=\s*\**\s*(VOLL|WENIG|LEER)", line, re.IGNORECASE)
        name_parts = []
        for p in (marke, auf):
            if p:
                val = p.group(1).strip().strip("*").strip()
                if val and "unleserlich" not in val.lower() and "[" not in val:
                    name_parts.append(val)
        name = " ".join(name_parts).strip(" -*·")
        if not name:
            continue
        items.append({"name": name[:80], "bestand": best.group(1).upper() if best else ""})
    return items


def save_scan(chat_id, description) -> bool:
    """Speichert den (geparsten) Regal-Scan als zuletzten Stand des Kunden."""
    if not chat_id:
        return False
    items = _parse(description)
    if not items:
        return False
    try:
        with _lock, sqlite3.connect(HISTORY_DB_PATH, timeout=5) as c:
            _ensure(c)
            c.execute("INSERT OR REPLACE INTO shelf_scans VALUES (?, ?, ?)",
                      (str(chat_id), time.time(), json.dumps(items, ensure_ascii=False)))
        log.info(f"shelf_inventory: Scan gespeichert ({len(items)} Produkte) fuer {chat_id}")
        return True
    except Exception as e:
        log.error(f"shelf save_scan: {e}")
        return False


def summary(chat_id):
    """{age_days, empty:[...], low:[...], total} des letzten Scans oder None."""
    if not chat_id:
        return None
    try:
        with sqlite3.connect(HISTORY_DB_PATH, timeout=5) as c:
            _ensure(c)
            row = c.execute("SELECT ts, items FROM shelf_scans WHERE chat_id=?",
                            (str(chat_id),)).fetchone()
        if not row:
            return None
        items = json.loads(row[1])
        return {
            "age_days": int((time.time() - row[0]) / 86400),
            "empty": [i["name"] for i in items if i.get("bestand") == "LEER"],
            "low": [i["name"] for i in items if i.get("bestand") == "WENIG"],
            "total": len(items),
        }
    except Exception as e:
        log.error(f"shelf summary: {e}")
        return None
