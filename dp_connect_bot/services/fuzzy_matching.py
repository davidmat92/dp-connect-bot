"""
Fuzzy matching & search – alias resolution, typo correction, search term extraction.
"""

from dp_connect_bot.config import CONFIRM_ALL, log
from dp_connect_bot.utils.formatting import kebab_to_readable

try:
    from thefuzz import fuzz
    HAS_FUZZY = True
except ImportError:
    HAS_FUZZY = False
    log.warning("thefuzz nicht installiert – Fuzzy Matching deaktiviert")


# Statische Aliases fuer haeufige Schreibvarianten
ALIASES = {
    "elfbar": "elf bar", "elf": "elf bar", "eb": "elf bar",
    "flerbar": "flerbar", "fler bar": "flerbar", "fler": "flerbar",
    "lostmary": "lost mary", "lm": "lost mary",
    "alfakher": "al fakher", "al faker": "al fakher", "alfaker": "al fakher",
    "almassiva": "al massiva", "al masiva": "al massiva",
    "randm": "rand m", "random": "rand m",
    "dinnerlady": "dinner lady", "barjuice": "bar juice",
    "vapeape": "vape ape", "badcandy": "bad candy",
    "onlygrams": "only grams", "lafume": "la fume",
    "mrbeast": "mr beast", "mr. beast": "mr beast",
    "aquamentha": "aqua mentha",
    "pod": "pods", "prefilled pod": "prefilled pods",
    "prefilled": "prefilled pods", "pre filled": "prefilled pods",
    "pre-filled": "prefilled pods",
    "elfbar800": "elf bar 800", "elfbar 800": "elf bar 800",
    "elf 800": "elf bar 800", "eb800": "elf bar 800",
    "elfa": "elfa", "tappo": "lost mary",
    "crystal": "crystal",
    "skecristal": "ske crystal", "ske cristal": "ske crystal",
    "nikotinfrei": "ohne nikotin", "nikotinfreie": "ohne nikotin",
    "nikotinfreien": "ohne nikotin", "nikotinfreier": "ohne nikotin",
    "0mg": "ohne nikotin", "0 mg": "ohne nikotin",
    "ohne nik": "ohne nikotin", "kein nikotin": "ohne nikotin",
    "shisha tabak": "tabak", "shishatabak": "tabak",
    "shisha": "tabak", "hookah": "tabak", "wasserpfeife": "tabak",
    "einweg": "einweg vape", "einweg vapes": "einweg vape",
    "einwegvapes": "einweg vape", "einwegvape": "einweg vape",
    "wegwerf": "einweg vape", "wegwerfvape": "einweg vape",
    "wegwerf vape": "einweg vape", "disposable": "einweg vape",
    "die kleinen": "einweg vape",
    "köpfe": "pods", "patronen": "pods", "kapseln": "pods",
    "kartuschen": "pods", "heads": "pods",
    "soße": "liquid", "sosse": "liquid", "juice": "liquid",
    "liquids": "liquid", "saft": "liquid",
    "dampfe": "vape", "dampfer": "vape", "e-zigarette": "vape",
    "e zigarette": "vape", "ezigarette": "vape",
    "kohle": "kohle", "naturkohle": "kohle", "shishakohle": "kohle",
    "snack": "snacks", "süßigkeiten": "snacks", "süßes": "snacks",
    "getränke": "drinks", "trinken": "drinks", "drink": "drinks",
    "energy": "energy drink", "energydrink": "energy drink",
    "elbar": "elf bar", "elfar": "elf bar", "els bar": "elf bar",
    "flerber": "flerbar", "flarbar": "flerbar", "flair bar": "flerbar",
    "lost mery": "lost mary", "los mary": "lost mary",
}

# Deutsche Geschmacksnamen -> Englische Suchbegriffe
FLAVOR_ALIASES_DE = {
    "birne": "pear", "pfirsich": "peach", "pfirsisch": "peach", "pfirisch": "peach",
    "kirsche": "cherry", "kirsch": "cherry",
    "erdbeere": "strawberry", "erdbeer": "strawberry", "erdbeeren": "strawberry",
    "wassermelone": "watermelon", "wasser melone": "watermelon",
    "wassermlone": "watermelon", "melone": "melon",
    "traube": "grape", "trauben": "grape", "weintraube": "grape",
    "apfel": "apple", "grüner apfel": "green apple",
    "zitrone": "lemon", "limette": "lime", "zitrus": "citrus",
    "mango": "mango", "ananas": "pineapple", "banane": "banana",
    "blaubeere": "blueberry", "blaubeeren": "blueberry",
    "himbeere": "raspberry", "himbeeren": "raspberry",
    "kokosnuss": "coconut", "kokos": "coconut",
    "minze": "mint", "pfefferminze": "mint", "menthol": "menthol",
    "kiwi": "kiwi", "tabak": "tobacco",
    "kaffee": "coffee", "cola": "cola",
    "brombeere": "blackberry", "brombeeren": "blackberry",
    "sahne": "cream", "eis": "ice",
    "tropisch": "tropical", "waldfrüchte": "forest berries",
    "passionsfrucht": "passion fruit", "guave": "guava", "maracuja": "passion fruit",
    "süßigkeiten": "candy", "bonbon": "candy",
    "haselnuss": "hazelnut", "karamell": "caramel",
    "chery": "cherry", "cheery": "cherry", "cherr": "cherry",
    "peatch": "peach", "peac": "peach",
    "watermlon": "watermelon", "watermlone": "watermelon",
    "blubery": "blueberry", "bluberry": "blueberry",
    "strwberry": "strawberry", "stawberry": "strawberry",
    "grap": "grape", "gape": "grape",
}


# Vokabular fuer Fuzzy-Korrektur (wird beim Cache-Laden aufgebaut)
_fuzzy_vocab = set()


def _build_fuzzy_vocab():
    """Baut Vokabular aus allen bekannten Woertern auf."""
    global _fuzzy_vocab
    from dp_connect_bot.services.product_cache import cache

    vocab = set()
    for de, en in FLAVOR_ALIASES_DE.items():
        vocab.add(de)
        vocab.add(en)
    for alias, target in ALIASES.items():
        vocab.add(alias)
        for w in target.split():
            vocab.add(w)
    if cache.all_products:
        for p in cache.all_products:
            if p.get("brand"):
                for w in p["brand"].lower().split():
                    vocab.add(w)
            for attr in ("geschmack", "auswahl", "farbe", "sorte"):
                val = kebab_to_readable(p.get(attr, "")).lower()
                for w in val.split():
                    if len(w) >= 3:
                        vocab.add(w)
            for cat in p.get("category", "").split("|"):
                for w in cat.strip().lower().split():
                    if len(w) >= 3:
                        vocab.add(w)
            for tag in p.get("tags", "").split(","):
                for w in tag.strip().lower().split():
                    if len(w) >= 3:
                        vocab.add(w)
    stopwords = {"und", "oder", "von", "für", "mit", "ohne", "ich", "mir", "mich",
                 "die", "der", "das", "ein", "eine", "den", "dem", "des",
                 "dann", "noch", "auch", "aber", "mal", "bitte", "gerne",
                 "rein", "dazu", "davon", "alle", "was", "wie", "hab",
                 "füge", "pack", "nimm", "gib", "zeig", "such", "will",
                 "brauche", "brauch", "möchte", "hätte", "guten", "gute",
                 "guter", "einen", "einer", "einem", "irgendwelchen"}
    _fuzzy_vocab = {w for w in vocab if len(w) >= 3} - stopwords
    log.info(f"Fuzzy-Vocab: {len(_fuzzy_vocab)} Woerter")


def normalize_query(text):
    """Wendet statische Aliases + deutsche Geschmacksnamen an."""
    text_lower = text.lower().strip()
    for alias in sorted(ALIASES.keys(), key=len, reverse=True):
        if alias in text_lower:
            text_lower = text_lower.replace(alias, ALIASES[alias])
    for de, en in sorted(FLAVOR_ALIASES_DE.items(), key=lambda x: -len(x[0])):
        if de in text_lower:
            text_lower = text_lower.replace(de, en)
    return text_lower


def fuzzy_match_brand(text):
    """Erkennt Marken auch bei Tippfehlern via Fuzzy Matching."""
    from dp_connect_bot.services.product_cache import cache

    if not HAS_FUZZY or not cache.brands:
        return text
    text_lower = text.lower()
    words = text_lower.split()
    replacements = {}
    brand_list = list(cache.brands)

    for i, word in enumerate(words):
        if len(word) < 3:
            continue
        candidates = [word]
        if i + 1 < len(words):
            candidates.append(f"{word} {words[i+1]}")
        if i + 2 < len(words):
            candidates.append(f"{word} {words[i+1]} {words[i+2]}")

        for candidate in candidates:
            best_score = 0
            best_brand = None
            for brand in brand_list:
                score = fuzz.ratio(candidate, brand)
                if score > best_score:
                    best_score = score
                    best_brand = brand
            if best_brand and 75 <= best_score < 100:
                replacements[candidate] = best_brand

    for wrong, correct in sorted(replacements.items(), key=lambda x: -len(x[0])):
        text_lower = text_lower.replace(wrong, correct)

    return text_lower


def fuzzy_correct_text(text):
    """Korrigiert Tippfehler im gesamten Text gegen bekanntes Vokabular."""
    if not HAS_FUZZY or not _fuzzy_vocab:
        return text

    words = text.lower().split()
    corrected = []
    vocab_list = list(_fuzzy_vocab)

    for word in words:
        if len(word) < 3 or word in _fuzzy_vocab:
            corrected.append(word)
            continue
        if word.replace(".", "").replace(",", "").isdigit():
            corrected.append(word)
            continue
        best_score = 0
        best_match = None
        for v in vocab_list:
            if abs(len(word) - len(v)) > 2:
                continue
            score = fuzz.ratio(word, v)
            if score > best_score:
                best_score = score
                best_match = v
        if best_match and best_score >= 80 and best_score < 100:
            log.debug(f"Fuzzy-Korrektur: '{word}' -> '{best_match}' (Score: {best_score})")
            corrected.append(best_match)
        else:
            corrected.append(word)

    return " ".join(corrected)


def extract_search_terms(text):
    """Extrahiert Suchbegriffe aus der Nutzer-Nachricht."""
    from dp_connect_bot.services.product_cache import cache

    text_lower = normalize_query(fuzzy_match_brand(fuzzy_correct_text(text)))
    terms = []

    category_words = {
        "pods", "vapes", "vape", "liquid", "liquids", "einweg",
        "snacks", "snack", "drinks", "drink", "energy",
        "tabak", "shisha", "cannabinoid", "h3", "h3bta", "h2",
        "snus", "kautabak", "leerpods", "prefilled pods",
        "akkuträger", "akku", "trendartikel",
        "ohne nikotin",
    }

    found_brands = []
    for brand in sorted(cache.brands, key=len, reverse=True):
        if brand in text_lower:
            found_brands.append(brand)
            terms.append(brand)

    for cat in category_words:
        if cat in text_lower:
            terms.append(cat)

    words = text_lower.split()
    for i, word in enumerate(words):
        if word in category_words and found_brands:
            for brand in found_brands:
                combined = f"{brand} {word}"
                if combined not in terms:
                    terms.append(combined)

    seen = set()
    unique = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            unique.append(t)

    if not unique and len(text.split()) <= 6:
        greetings = CONFIRM_ALL | {"hi", "hallo", "hey", "moin", "servus", "na", "yo", "moinsen"}
        if not set(text_lower.split()).issubset(greetings):
            unique = [text_lower]

    return unique
