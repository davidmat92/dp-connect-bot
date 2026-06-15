#!/usr/bin/env python3
"""Audit-Runde 2: Verständnis-Tiefe + Retest der gefixten Fälle."""
import json, os, time, urllib.request

BASE = "https://bot-dpconnect.pythonanywhere.com"
KEY = os.environ.get("BOT_ADMIN_KEY", "")

CASES = [
    # Retest gefixte Fälle
    ("retest_wiederholung", 427, ["elf bar 800", "elf bar 800", "elf bar 800 jetzt!"]),
    # Verständnis-Tiefe
    ("die_billigsten", 427, ["was sind eure günstigsten einweg vapes?"]),
    ("empfehlung", 427, ["was empfiehlst du mir, läuft gerade gut?"]),
    ("vage_geschmack", 427, ["irgendwas mit minze für die elf bar 800"]),
    ("produktwissen", 427, ["wie viele züge hat die elf bar 800?"]),
    ("ist_mit_nikotin", 427, ["ist die elfa pods mit nikotin?"]),
    ("negation", 427, ["zeig mir elf bar 800 sorten, aber keine fruchtigen"]),
    # Mengeneinheiten
    ("karton", 427, ["ich brauch 2 kartons elf bar 800 blueberry"]),
    ("stange", 427, ["5 stangen elfa pods peach ice"]),
    # Korrektur-Varianten
    ("halbe_menge", 427, ["40 elf bar 800 cherry", "ach mach die hälfte"]),
    ("doch_weniger", 427, ["100 elfa pods peach ice", "doch nicht so viele, nur 30"]),
    # Smalltalk / Meta
    ("bist_du_bot", 427, ["bist du eigentlich ein echter mensch oder ein bot?"]),
    ("guten_morgen", 427, ["guten morgen! na alles fit?"]),
    # Verifizierungs-Übergang (unverifiziert fragt Preis, dann beraten)
    ("interessent_beratung", 0, ["ich hab nen kiosk und überlege bei euch zu bestellen, was habt ihr denn so?"]),
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
            init = {"visitor_id": f"audit2-{name}-{int(time.time())}"}
            if uid:
                init.update({"wp_user_id": uid, "customer_name": "Audit"})
            chat = post("/chat/init", init)["chat_id"]
            print(f"\n{'='*72}\n## {name}  (uid={uid})\n{'='*72}")
            for m in msgs:
                print(f">>> {m}")
                try:
                    resp = post("/chat/send", {"chat_id": chat, "message": m})
                    print(f"<<< {resp.get('text','(keine)')}")
                except Exception as e:
                    print(f"!! FEHLER: {e}")
    finally:
        cfg({"enabled": False, "order_enabled": False})


if __name__ == "__main__":
    main()
