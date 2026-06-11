#!/usr/bin/env python3
"""
End-to-End-Chatflow-Tests gegen den laufenden Bot (Webchat-API).

Usage:
    python3 scripts/chat_e2e.py                # alle Szenarien
    python3 scripts/chat_e2e.py multi_item     # nur ein Szenario

Vorbedingung: web.order_enabled muss aktiv sein (Admin-Key via env
BOT_ADMIN_KEY setzen, dann schaltet das Skript selbst um und zurueck).
"""

import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("BOT_BASE", "https://bot-dpconnect.pythonanywhere.com")
ADMIN_KEY = os.environ.get("BOT_ADMIN_KEY", "")

# Szenarien: (name, [nachrichten])
SCENARIOS = {
    "multi_item": [
        "Hi, ich würde gerne 20 x Elflick bestellen, Geschmack Pfirsich mit 20 Milligramm und 50 Elfapots auch mit Pfirsich.",
        "Peach Ice",
    ],
    "vage_anfrage": [
        "habt ihr auch was fruchtiges ohne nikotin für den laden?",
    ],
    "preisfrage": [
        "was kostet die elfbar 800 bei euch?",
    ],
    "menge_aendern": [
        "pack mir 20 elf bar 800 blueberry ein",
        "mach lieber 30 draus",
    ],
    "ablehnung": [
        "20 elf bar 800 watermelon bitte",
        "ne doch nicht, lass die watermelon weg",
    ],
    "anapher": [
        "zeig mir mal eure shisha tabak sorten",
        "ok nimm 2x die erste sorte",
    ],
    "checkout": [
        "10 elf bar 800 blueberry",
        "das wars, bestellen bitte",
    ],
    "tippfehler": [
        "hast du elfbar600 blaubere?",
    ],
    "support_bestellstatus": [
        "wo bleibt meine bestellung 99999?",
    ],
    "eskalation_mitten_drin": [
        "20 elf bar 800 cherry",
        "ich will lieber mit einem menschen sprechen",
    ],
    "warenkorb_anzeige": [
        "10 elf bar 800 blueberry",
        "was hab ich jetzt im warenkorb?",
    ],
    "kategorien_browse": [
        "was habt ihr denn so alles?",
    ],
    "staffelpreis": [
        "was kosten die elfa pods bei größeren mengen?",
    ],
    "englisch": [
        "hi, do you have elf bar 800 in watermelon? need 20 pieces",
    ],
    "smalltalk_dank": [
        "10 elf bar 800 cherry",
        "super danke dir!",
    ],
    "nachbestellen": [
        "10 elf bar 800 cherry",
        "das wars, bestellen",
        "hi, nochmal das gleiche wie letztes mal bitte",
    ],
}


def post(path, payload, timeout=120):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_web_config():
    req = urllib.request.Request(
        f"{BASE}/admin/config",
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    return (data.get("config", {}).get("channels", {}) or {}).get("web", {})


def set_web_config(flags):
    req = urllib.request.Request(
        f"{BASE}/admin/config",
        data=json.dumps({"channels": {"web": flags}}).encode(),
        headers={"Content-Type": "application/json", "X-Admin-Key": ADMIN_KEY},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("ok", False)


def run_scenario(name, messages):
    visitor = f"e2e-{name}-{int(time.time())}"
    init = post("/chat/init", {
        "visitor_id": visitor,
        "wp_user_id": 1,
        "wp_display_name": "E2E Test",
        "customer_name": "E2E Test",
    })
    chat_id = init["chat_id"]
    print(f"\n{'=' * 70}\n## {name}  ({chat_id})\n{'=' * 70}")
    for msg in messages:
        print(f"\n>>> KUNDE: {msg}")
        t0 = time.time()
        resp = post("/chat/send", {"chat_id": chat_id, "message": msg})
        dt = time.time() - t0
        text = resp.get("text", "(keine Antwort)")
        kb = resp.get("keyboards") or []
        print(f"<<< BOT ({dt:.1f}s): {text}")
        if kb:
            print(f"    [Keyboards: {[k.get('type') for k in kb]}]")


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    scenarios = {only: SCENARIOS[only]} if only else SCENARIOS

    saved = None
    if ADMIN_KEY:
        saved = get_web_config()
        set_web_config({"enabled": True, "order_enabled": True})
        print(f"Web-Kanal temporaer AN (vorher: {saved})")
    try:
        for name, msgs in scenarios.items():
            try:
                run_scenario(name, msgs)
            except Exception as e:
                print(f"!! Szenario {name} fehlgeschlagen: {e}")
    finally:
        if saved is not None:
            # Exakten Vorzustand wiederherstellen (fehlende Keys = Defaults)
            restore = {
                "enabled": saved.get("enabled", True),
                "order_enabled": saved.get("order_enabled", True),
            }
            if "voice_enabled" in saved:
                restore["voice_enabled"] = saved["voice_enabled"]
            set_web_config(restore)
            print(f"\nWeb-Kanal wiederhergestellt: {restore}")


if __name__ == "__main__":
    main()
