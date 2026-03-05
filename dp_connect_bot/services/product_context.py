"""
Product context builder – creates the product context string for Claude AI.
"""

from dp_connect_bot.services.product_cache import cache, ensure_cache
from dp_connect_bot.services.fuzzy_matching import extract_search_terms
from dp_connect_bot.services.history import track_search_query
from dp_connect_bot.utils.formatting import (
    format_price_de, get_variant_display_name, get_variant_type_label,
    pipe_to_list, stock_label,
)


def build_product_context(user_message):
    """Baut den Produktkontext fuer Claude basierend auf der Nutzer-Nachricht."""
    ensure_cache()
    search_terms = extract_search_terms(user_message)
    parts = []

    if not search_terms:
        parts.append(get_category_overview())
        return "\n".join(parts)

    for term in search_terms:
        is_brand_search = term.lower() in [b.lower() for b in cache.brands]

        if is_brand_search:
            brand_parents = cache.get_parents_available(brand=term)
            available = []
            for p in brand_parents:
                avail_vars = cache.get_variations_available(p["id"])
                available.append(p)
                available.extend(avail_vars)

            if not available:
                brand_variations = [p for p in cache.available
                                    if p.get("brand", "").lower() == term.lower()
                                    and p.get("produkt_typ") == "variation"]
                if brand_variations:
                    available = brand_variations
                    for v in brand_variations:
                        pid = v.get("post_parent")
                        if pid and pid not in [p.get("id") for p in available]:
                            parent = cache.get_product_by_id(pid)
                            if parent:
                                available.insert(0, parent)

            all_found = cache.search_all(term, max_results=50)
        else:
            available = cache.search_available(term)
            all_found = cache.search_all(term)

        track_search_query(term, "", "", len(available))

        if available:
            simple_products = []
            parent_ids_seen = set()
            grouped_parents = []

            for p in available:
                parent_id = p.get("post_parent")
                if parent_id and parent_id != "0" and parent_id != "":
                    if parent_id not in parent_ids_seen:
                        parent_ids_seen.add(parent_id)
                        parent = cache.get_product_by_id(parent_id)
                        avail_vars = cache.get_variations_available(parent_id)
                        if parent and avail_vars:
                            grouped_parents.append((parent, avail_vars))
                        elif avail_vars:
                            grouped_parents.append((None, avail_vars))
                else:
                    pid = p.get("id")
                    if pid not in parent_ids_seen:
                        parent_ids_seen.add(pid)
                        avail_vars = cache.get_variations_available(pid)
                        if avail_vars:
                            grouped_parents.append((p, avail_vars))
                        else:
                            simple_products.append(p)

            def bestseller_sort(item):
                parent, variations = item if isinstance(item, tuple) else (item, [])
                check = parent or (variations[0] if variations else None)
                if not check:
                    return 1
                cats = check.get("category", "").lower()
                return 0 if "bestseller" in cats else 1

            grouped_parents.sort(key=bestseller_sort)
            simple_products.sort(key=lambda p: 0 if "bestseller" in p.get("category", "").lower() else 1)

            parts.append(f"\n=== VERFUEGBAR fuer '{term}' ===")

            parent_limit = 20 if is_brand_search else 10
            for parent, variations in grouped_parents[:parent_limit]:
                if parent:
                    is_bs = "bestseller" in parent.get("category", "").lower()
                    if not is_bs and variations:
                        is_bs = any("bestseller" in v.get("category", "").lower() for v in variations)
                    parts.append(format_parent_with_variations(parent, variations, is_bestseller=is_bs))
                elif variations:
                    is_bs = any("bestseller" in v.get("category", "").lower() for v in variations)
                    parts.append(format_orphan_variations(variations, is_bestseller=is_bs))

            for p in simple_products[:5]:
                is_bs = "bestseller" in p.get("category", "").lower()
                bs_tag = " ⭐BESTSELLER" if is_bs else ""
                price = format_price_de(p.get("price"))
                vpe = f" | VPE: {p['vpe']}" if p.get("vpe") else ""
                parts.append(f"\n  {p['title']} [ID:{p['id']}] | {price}{vpe}{bs_tag}")

        elif all_found:
            parts.append(f"\n=== '{term}' NICHT LIEFERBAR ===")
            for p in all_found[:5]:
                parts.append(f"  - {p['title']} (ausverkauft)")
            if all_found[0].get("category"):
                cats = all_found[0]["category"].split("|")
                for cat in cats:
                    cat = cat.strip()
                    if cat and cat.lower() != "bestseller":
                        alts = cache.get_parents_available(category=cat)
                        if alts:
                            parts.append(f"\n  Verfuegbare Alternativen in '{cat}':")
                            for a in alts[:5]:
                                parts.append(f"  - {a['title']} [ID:{a['id']}]")
                        break
        else:
            parts.append(f"\nKeine Produkte gefunden fuer '{term}'.")

    return "\n".join(parts)


def format_parent_with_variations(parent, avail_vars=None, is_bestseller=False):
    """Formatiert ein Parent-Produkt mit allen verfuegbaren + nicht-lieferbaren Varianten."""
    lines = []
    price = format_price_de(parent.get("price")) if parent.get("price") else ""
    vpe = f" | VPE: {parent['vpe']}" if parent.get("vpe") else ""
    sl = stock_label(parent.get("stock"))
    stock_str = f" | {sl}" if sl else ""
    bs_tag = " ⭐BESTSELLER" if is_bestseller else ""

    lines.append(f"\nProdukt: {parent['title']} [ID:{parent['id']}]{' | ' + price if price else ''}{vpe}{stock_str}{bs_tag}")
    lines.append(f"  Marke: {parent.get('brand', '?')} | Kategorie: {parent.get('category', '?')}")

    if parent.get("nikotingehalt"):
        lines.append(f"  Nikotin: {parent['nikotingehalt']}")

    if avail_vars is None:
        avail_vars = cache.get_variations_available(parent["id"])
    all_vars = cache.get_variations_all(parent["id"])

    if avail_vars:
        first_avail_vpe = avail_vars[0].get("vpe", "")
        if first_avail_vpe and first_avail_vpe != parent.get("vpe", ""):
            old_vpe = vpe
            vpe = f" | VPE: {first_avail_vpe}"
            lines[0] = lines[0].replace(old_vpe, vpe)
        elif not parent.get("vpe") and first_avail_vpe:
            vpe = f" | VPE: {first_avail_vpe}"
            lines[0] = lines[0].replace(stock_str, f"{vpe}{stock_str}")

        vtype = get_variant_type_label(avail_vars[0])
        lines.append(f"\n  Verfuegbare {vtype}en ({len(avail_vars)} von {len(all_vars) if all_vars else len(avail_vars)}):")

        for v in avail_vars:
            name = get_variant_display_name(v)
            vprice = format_price_de(v.get("price"))
            vsl = stock_label(v.get("stock"))
            vstock_str = f" | {vsl}" if vsl else ""
            sonder_str = ""
            sp = v.get("sonderpreis")
            sp_min = v.get("sonderpreis_min")
            if sp and sp_min:
                try:
                    sp_val = float(sp)
                    sp_min_val = int(float(sp_min))
                    if sp_val > 0 and sp_min_val > 0:
                        sonder_str = f" | Sonderpreis ab {sp_min_val} Stk: {format_price_de(sp_val)}"
                except (ValueError, TypeError):
                    pass
            lines.append(f"    - {name} [ID:{v['id']}] | {vprice}{vstock_str}{sonder_str}")

        unavail = [v for v in all_vars if not cache.is_available(v["id"])]
        if unavail:
            names = [get_variant_display_name(v) for v in unavail]
            lines.append(f"\n  NICHT LIEFERBAR ({len(unavail)}): {', '.join(names[:15])}")

    else:
        parent_flavors = pipe_to_list(parent.get("geschmack", ""))
        parent_auswahl = pipe_to_list(parent.get("auswahl", ""))
        parent_sorten = pipe_to_list(parent.get("sorte", ""))
        all_options = parent_flavors or parent_auswahl or parent_sorten
        if all_options:
            lines.append(f"\n  Varianten laut Katalog: {', '.join(all_options[:20])}")

    return "\n".join(lines)


def format_orphan_variations(variations, is_bestseller=False):
    """Formatiert Variationen deren Parent nicht in der DB ist."""
    if not variations:
        return ""
    first = variations[0]
    brand = first.get("brand", "")
    title_parts = first.get("title", "").rsplit(" - ", 1)
    group_name = title_parts[0] if len(title_parts) > 1 else first.get("title", "")
    bs_tag = " ⭐BESTSELLER" if is_bestseller else ""
    vpe = f" | VPE: {first['vpe']}" if first.get("vpe") else ""

    lines = [f"\nProdukt: {group_name}{vpe}{bs_tag}"]
    lines.append(f"  Marke: {brand}")

    vtype = get_variant_type_label(first)
    lines.append(f"\n  Verfuegbare {vtype}en ({len(variations)}):")
    for v in variations:
        name = get_variant_display_name(v)
        vprice = format_price_de(v.get("price"))
        vsl = stock_label(v.get("stock"))
        vstock_str = f" | {vsl}" if vsl else ""
        lines.append(f"    - {name} [ID:{v['id']}] | {vprice}{vstock_str}")

    return "\n".join(lines)


def format_variation_list(variations):
    """Formatiert eine Liste von Variationen."""
    lines = []
    for v in variations:
        name = get_variant_display_name(v)
        price = format_price_de(v.get("price"))
        vpe = f" | VPE: {v['vpe']}" if v.get("vpe") else ""
        stock = f" | Lager: {v['stock']}" if v.get("stock") else ""
        lines.append(f"  - {name} [ID:{v['id']}] | {price}{vpe}{stock}")
    return "\n".join(lines)


def get_category_overview():
    """Uebersicht der verfuegbaren Produktkategorien."""
    ensure_cache()
    cats = {}
    for p in cache.available:
        if not p.get("post_parent"):
            for cat in p.get("category", "Sonstiges").split("|"):
                cat = cat.strip()
                if cat:
                    cats[cat] = cats.get(cat, 0) + 1
    lines = ["Verfuegbare Produktkategorien bei DP Connect:"]
    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        lines.append(f"  - {cat} ({count} Produkte)")
    return "\n".join(lines)
