"""
Onboarding hint system – shows contextual tips once per session.
"""


HINTS = {
    "first_cart_add": "\n\n💡 _Tipp: Schreib *fertig* wenn du bestellen willst!_",
    "checkout_done": "\n\n💡 _Tipp: Nächstes Mal einfach *nochmal* schreiben für die gleiche Bestellung!_",
    "voice_available": "\n\n🎤 _Du kannst mir auch eine Sprachnachricht schicken!_",
    "search_hint": "\n\n💡 _Tipp: Sag mir einfach was du brauchst – z.B. \"Elf Bar\", \"Pods\" oder \"50 Cherry\"_",
    "multi_order": "\n\n💡 _Tipp: Du kannst auch mehrere Sachen auf einmal bestellen – z.B. \"50 Cherry, 30 Peach, 20 Mint\"_",
}


def get_hint(session, hint_key):
    """Gibt einen kontextuellen Hint zurueck, aber nur EINMAL pro Session.

    Returns: Hint-Text oder "" wenn bereits gezeigt.
    """
    hints = session.setdefault("hints_shown", {})
    if hints.get(hint_key):
        return ""
    hints[hint_key] = True
    return HINTS.get(hint_key, "")
