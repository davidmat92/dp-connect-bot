#!/usr/bin/env python3
"""
Qualitäts-Audit: spielt frustträchtige Edge-Cases durch und zeigt die
Antworten kompakt, um Logik-/Verständnis-Probleme zu finden.

Usage: BOT_ADMIN_KEY=... python3 scripts/chat_audit.py
"""
import json, os, time, urllib.request

BASE = "https://bot-dpconnect.pythonanywhere.com"
KEY = os.environ.get("BOT_ADMIN_KEY", "")

# (name, wp_user_id (0=unverifiziert), [nachrichten])  — Frust-Edge-Cases
CASES = [
    # --- Mehrdeutige / knappe Eingaben ---
    ("ja_ohne_kontext", 427, ["ja"]),
    ("knapp_der_erste", 427, ["was habt ihr von elf bar?", "den ersten"]),
    ("themenwechsel", 427, ["20 elf bar 800 cherry", "ach zeig mir lieber shisha tabak"]),
    ("mehrere_fragen", 427, ["habt ihr elfliq und was kostet die elf bar 800 und gibt's lost mary?"]),
    # --- Bestell-Logik Grenzen ---
    ("menge_null", 427, ["0 elf bar 800 cherry"]),
    ("menge_negativ", 427, ["minus 5 elf bar 800"]),
    ("menge_riesig", 427, ["1 million elf bar 800 blueberry"]),
    ("erfundenes_produkt", 427, ["habt ihr die Glubschi Mega Vape 5000?"]),
    ("alles_raus", 427, ["10 elf bar 800 cherry", "ach lösch alles"]),
    ("leerer_checkout", 427, ["kasse bitte"]),
    # --- Verifizierung Edge-Cases ---
    ("falsche_email", 0, ["was kostet die elf bar 800?", "quatschadresse@gibtsnicht-xyz.de"]),
    ("kein_at_email", 0, ["preise bitte", "meinemail punkt de"]),
    ("code_ohne_anfrage", 0, ["123456"]),
    # --- Self-Service Grenzen ---
    ("tracking_ohne_bestellung", 290, ["wo bleibt meine bestellung?"]),
    ("rechnung_ohne_bestellung", 290, ["schick mir meine rechnung"]),
    ("fremde_bestellnummer", 427, ["was war in bestellung 99999?"]),
    # --- Frust-Klassiker ---
    ("wiederholung", 427, ["elf bar 800", "elf bar 800", "ELF BAR 800 will ich!!"]),
    ("genervt", 427, ["20 elf bar 800 cherry", "nein das wollte ich nicht, du verstehst mich nicht"]),
    ("nur_emoji", 427, ["👍"]),
    ("unsinn", 427, ["asdfghjkl qwertz"]),
    # --- Komplexe Realszenarien ---
    ("korrektur_kette", 427, ["30 elf bar 800 cherry", "ne 20", "und davon die hälfte ohne nikotin"]),
    ("mengen_mix", 427, ["50 cherry, 30 peach und 20 watermelon elf bar 800"]),
]


def post(path, payload, timeout=120):
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def cfg(flags):
    req = urllib.request.Request(f"{BASE}/admin/config",
                                 data=json.dumps({"channels": {"web": flags}}).encode(),
                                 headers={"Content-Type": "application/json", "X-Admin-Key": KEY}, method="POST")
    urllib.request.urlopen(req, timeout=30).read()


def main():
    cfg({"enabled": True, "order_enabled": True})
    try:
        for name, uid, msgs in CASES:
            init = {"visitor_id": f"audit-{name}-{int(time.time())}"}
            if uid:
                init.update({"wp_user_id": uid, "wp_display_name": "Audit", "wp_email": f"audit{uid}@x.de", "customer_name": "Audit"})
            chat = post("/chat/init", init)["chat_id"]
            print(f"\n{'='*72}\n## {name}  (uid={uid})\n{'='*72}")
            for m in msgs:
                print(f">>> {m}")
                try:
                    t0 = time.time()
                    resp = post("/chat/send", {"chat_id": chat, "message": m})
                    txt = resp.get("text", "(keine Antwort)")
                    kb = [k.get("type") for k in (resp.get("keyboards") or [])]
                    print(f"<<< ({time.time()-t0:.1f}s) {txt}" + (f"  [KB:{kb}]" if kb else ""))
                except Exception as e:
                    print(f"!! FEHLER: {e}")
    finally:
        cfg({"enabled": False, "order_enabled": False})


if __name__ == "__main__":
    main()
