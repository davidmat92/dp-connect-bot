"""
Product Cache – loads and indexes products from WooCommerce (primary) or Airtable (fallback).
"""

import html
import json
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from threading import Lock, Thread

from dp_connect_bot.config import (
    AIRTABLE_PAT, AIRTABLE_BASE_ID, AIRTABLE_TABLE_ID,
    AIRTABLE_VIEW_AVAILABLE, AIRTABLE_VIEW_ALL,
    WOOCOMMERCE_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET,
    CACHE_REFRESH_MINUTES, PRODUCT_SNAPSHOT_PATH, log,
)
from dp_connect_bot.utils.formatting import kebab_to_readable

_on_cache_loaded = None


def set_on_cache_loaded(callback):
    """Register a callback that is called after cache.load() finishes."""
    global _on_cache_loaded
    _on_cache_loaded = callback


class ProductCache:
    def __init__(self):
        self.available = []
        self.all_products = []
        self.brands = set()
        self.categories = set()
        self.last_loaded = None
        self.source = None
        self.lock = Lock()
        self._refresh_lock = Lock()
        self._refreshing = False

    def needs_refresh(self):
        if not self.last_loaded:
            return True
        return (datetime.now() - self.last_loaded).total_seconds() / 60 > CACHE_REFRESH_MINUTES

    def load(self):
        available, all_prods = None, None
        source = None

        if WC_CONSUMER_KEY and WC_CONSUMER_SECRET:
            log.info("Lade Produktdaten aus WooCommerce...")
            try:
                available, all_prods = self._load_from_woocommerce()
                if available is not None and all_prods is not None:
                    source = "WooCommerce"
            except Exception as e:
                log.error(f"WooCommerce-Fehler: {e}")

        if available is None:
            log.info("Lade Produktdaten aus Airtable (Fallback)...")
            at_available = self._load_airtable_view(AIRTABLE_VIEW_AVAILABLE)
            at_all = self._load_airtable_view(AIRTABLE_VIEW_ALL)
            if at_available is not None:
                available = at_available
                all_prods = at_all
                source = "Airtable"

        with self.lock:
            if available is not None:
                self.available = available
            if all_prods is not None:
                self.all_products = all_prods
            self._build_indices()
            self.last_loaded = datetime.now()
            self.source = source

        log.info(f"Geladen ({source}): {len(self.available)} verfuegbar, {len(self.all_products)} gesamt, {len(self.brands)} Marken")
        if available is not None:
            self.save_snapshot()
        if _on_cache_loaded:
            _on_cache_loaded()

    def refresh_in_background(self):
        """Startet einen Refresh in einem Background-Thread (non-blocking).
        Alte Daten bleiben verfuegbar bis der neue Load fertig ist."""
        with self._refresh_lock:
            if self._refreshing:
                return
            self._refreshing = True

        def _worker():
            try:
                self.load()
            except Exception as e:
                log.error(f"Background-Refresh fehlgeschlagen: {e}")
            finally:
                with self._refresh_lock:
                    self._refreshing = False

        Thread(target=_worker, daemon=True).start()
        log.info("Background-Refresh gestartet")

    # ------------------------------------------------------------------
    # Disk-Snapshot (schneller Start nach Webapp-Reload)
    # ------------------------------------------------------------------

    def save_snapshot(self):
        try:
            data = {
                "saved_at": datetime.now().isoformat(),
                "source": self.source,
                "available": self.available,
                "all_products": self.all_products,
            }
            tmp_path = PRODUCT_SNAPSHOT_PATH + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp_path, PRODUCT_SNAPSHOT_PATH)
            log.info(f"Snapshot gespeichert: {len(self.all_products)} Produkte")
        except Exception as e:
            log.error(f"Snapshot speichern fehlgeschlagen: {e}")

    def load_snapshot(self):
        """Laedt den letzten Snapshot von Disk. Gibt True bei Erfolg zurueck."""
        try:
            if not os.path.exists(PRODUCT_SNAPSHOT_PATH):
                return False
            with open(PRODUCT_SNAPSHOT_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            saved_at = datetime.fromisoformat(data["saved_at"])
            with self.lock:
                self.available = data["available"]
                self.all_products = data["all_products"]
                self._build_indices()
                self.last_loaded = saved_at
                self.source = f"{data.get('source', '?')} (Snapshot)"
            age_min = (datetime.now() - saved_at).total_seconds() / 60
            log.info(f"Snapshot geladen ({age_min:.0f} Min alt): {len(self.available)} verfuegbar, {len(self.all_products)} gesamt")
            if _on_cache_loaded:
                _on_cache_loaded()
            return True
        except Exception as e:
            log.error(f"Snapshot laden fehlgeschlagen: {e}")
            return False

    # ------------------------------------------------------------------
    # WooCommerce
    # ------------------------------------------------------------------

    def _load_from_woocommerce(self):
        base_url = f"{WOOCOMMERCE_URL}/wp-json/wc/v3/products"
        auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)

        main_products = self._wc_paginate(base_url, auth, params={"status": "publish"})
        if main_products is None:
            return None, None

        all_normalized = []
        variable_parents = []
        for p in main_products:
            all_normalized.append(self._normalize_wc(p))
            if p.get("type") == "variable":
                variable_parents.append(p)

        def fetch_variations(parent):
            var_url = f"{base_url}/{parent['id']}/variations"
            variations = self._wc_paginate(var_url, auth)
            if variations is None:
                log.warning(f"Variationen fuer Produkt {parent['id']} ({parent.get('name','')}) uebersprungen")
                return []
            return [self._normalize_wc(v, parent=parent) for v in variations]

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(fetch_variations, p): p for p in variable_parents}
            for future in as_completed(futures):
                try:
                    all_normalized.extend(future.result())
                except Exception as e:
                    p = futures[future]
                    log.warning(f"Variationen fuer {p['id']} fehlgeschlagen: {e}")

        # Sonderpreise werden NUR in Airtable gepflegt (nicht in WC) — anreichern,
        # sonst bietet der Bot keine Mengenrabatte mehr an!
        sonder = self._load_sonderpreise_from_airtable()
        if sonder:
            hits = 0
            for p in all_normalized:
                sp = sonder.get(p["id"])
                if sp:
                    p["sonderpreis"], p["sonderpreis_min"] = sp
                    hits += 1
            log.info(f"Sonderpreise aus Airtable angereichert: {hits} Produkte")

        available = [p for p in all_normalized if p["stock_status"] == "instock"]
        return available, all_normalized

    def _load_sonderpreise_from_airtable(self):
        """Holt nur die Sonderpreis-Felder aus Airtable: {id: (preis, min_anzahl)}."""
        if not (AIRTABLE_PAT and AIRTABLE_BASE_ID and AIRTABLE_TABLE_ID):
            return {}
        headers = {"Authorization": f"Bearer {AIRTABLE_PAT}"}
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}"
        result = {}
        offset = None
        try:
            while True:
                params = {
                    "pageSize": 100,
                    "filterByFormula": "AND({(sonder) PREIS} != '', {(sonder) MinAnzahl} != '')",
                    "fields[]": ["ID", "(sonder) PREIS", "(sonder) MinAnzahl"],
                }
                if offset:
                    params["offset"] = offset
                resp = requests.get(url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                for r in data.get("records", []):
                    f = r.get("fields", {})
                    pid = str(f.get("ID", "")).strip()
                    if pid:
                        result[pid] = (str(f.get("(sonder) PREIS", "")), str(f.get("(sonder) MinAnzahl", "")))
                offset = data.get("offset")
                if not offset:
                    break
        except Exception as e:
            log.error(f"Sonderpreis-Anreicherung fehlgeschlagen: {e}")
        return result

    def _wc_paginate(self, url, auth, params=None):
        all_items = []
        page = 1
        base_params = dict(params or {})
        while True:
            req_params = {**base_params, "per_page": 100, "page": page}
            try:
                resp = requests.get(url, auth=auth, params=req_params, timeout=30)
                resp.raise_for_status()
                items = resp.json()
                if not items:
                    break
                all_items.extend(items)
                if len(items) < 100:
                    break
                page += 1
            except Exception as e:
                log.error(f"WooCommerce-Fehler ({url}, Seite {page}): {e}")
                return None
        return all_items

    def _normalize_wc(self, p, parent=None):
        is_variation = parent is not None

        var_name = p.get("name", "")
        if is_variation:
            parent_name = parent.get("name", "")
            if var_name and var_name != parent_name:
                title = f"{parent_name} - {var_name}"
            elif not var_name:
                attr_parts = [a.get("option", "") for a in p.get("attributes", []) if a.get("option")]
                title = parent_name + (" - " + " - ".join(attr_parts) if attr_parts else "")
            else:
                title = var_name
        else:
            title = var_name

        source_title = parent.get("name", title) if is_variation else title
        brand, product_line, flavor = self._parse_title(source_title)

        ref = parent or p
        for a in ref.get("attributes", []):
            if a["name"].lower() in ("marke", "brand", "pa_marke"):
                opts = a.get("options", [a.get("option", "")])
                if isinstance(opts, list) and opts:
                    brand = opts[0]
                elif isinstance(opts, str) and opts:
                    brand = opts
                break

        cat_source = parent if is_variation else p
        cats = "|".join(c.get("name", "") for c in cat_source.get("categories", []))
        tags = ",".join(t.get("name", "") for t in cat_source.get("tags", []))

        if is_variation:
            image = p.get("image") or {}
            image_url = image.get("src", "")
            if not image_url:
                images = parent.get("images", [])
                image_url = images[0].get("src", "") if images else ""
        else:
            images = p.get("images", [])
            image_url = images[0].get("src", "") if images else ""

        def get_attr(*names):
            names_lower = [n.lower() for n in names]
            for a in p.get("attributes", []):
                if a["name"].lower() in names_lower:
                    opt = a.get("option", "")
                    if opt:
                        return opt
                    opts = a.get("options", [])
                    return ", ".join(str(o) for o in opts) if opts else ""
            return ""

        meta = {m["key"]: m["value"] for m in p.get("meta_data", [])}
        parent_meta = {m["key"]: m["value"] for m in parent.get("meta_data", [])} if parent else {}

        geschmack = get_attr("geschmack", "pa_geschmack", "flavor", "flavour")
        if is_variation and not flavor:
            flavor = geschmack

        price = str(p.get("regular_price", "") or p.get("price", "") or "")
        if not price and parent:
            price = str(parent.get("regular_price", "") or parent.get("price", "") or "")

        # WooCommerce liefert Namen/Kategorien HTML-encoded ("Tabak &amp; Kohle",
        # "Cola &amp; Orange") — dekodieren, sonst verschmutzt es Suche und Kontext
        u = html.unescape
        return {
            "id": str(p.get("id", "")),
            "post_parent": str(parent["id"] if is_variation else (p.get("parent_id", "") or "")),
            "title": u(title),
            "brand": u(brand),
            "product_line": u(product_line),
            "flavor": u(flavor),
            "produkt_typ": "variation" if is_variation else p.get("type", ""),
            "geschmack": u(geschmack),
            "farbe": u(get_attr("farbe", "pa_farbe", "color", "colour")),
            "auswahl": u(get_attr("auswahl", "pa_auswahl", "selection")),
            "sorte": u(get_attr("sorte", "pa_sorte", "variety")),
            "nikotingehalt": u(get_attr("nikotingehalt", "pa_nikotingehalt", "nicotine", "nikotin")),
            "price": price,
            "sonderpreis": str(p.get("sale_price", "") or ""),
            "sonderpreis_min": str(meta.get("_sonderpreis_min", parent_meta.get("_sonderpreis_min", "")) or ""),
            "stock_status": p.get("stock_status", ""),
            "stock": str(p.get("stock_quantity", "") or ""),
            "category": u(cats),
            "vpe": str(meta.get("_vpe", parent_meta.get("_vpe", "")) or get_attr("vpe", "pa_vpe") or ""),
            "url": p.get("permalink", (parent or {}).get("permalink", "")),
            "sku": p.get("sku", ""),
            "tags": u(tags),
            "image_url": image_url,
        }

    # ------------------------------------------------------------------
    # Airtable (Fallback)
    # ------------------------------------------------------------------

    def _load_airtable_view(self, view_id):
        headers = {"Authorization": f"Bearer {AIRTABLE_PAT}"}
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}"
        all_records = []
        offset = None
        while True:
            params = {"pageSize": 100, "view": view_id}
            if offset:
                params["offset"] = offset
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                all_records.extend(data.get("records", []))
                offset = data.get("offset")
                if not offset:
                    break
            except Exception as e:
                log.error(f"Airtable-Fehler (view {view_id}): {e}")
                return None
        return [self._normalize_airtable(r.get("fields", {})) for r in all_records]

    def _normalize_airtable(self, f):
        title = f.get("post_title", "")
        brand, product_line, flavor = self._parse_title(title)
        brand = f.get("marke", "") or brand
        return {
            "id": str(f.get("ID", "")),
            "post_parent": str(f.get("post_parent", "") or ""),
            "title": title,
            "brand": brand,
            "product_line": product_line,
            "flavor": flavor,
            "produkt_typ": f.get("produkt_typ", ""),
            "geschmack": str(f.get("geschmack", "") or ""),
            "farbe": str(f.get("farbe", "") or ""),
            "auswahl": str(f.get("auswahl", "") or ""),
            "sorte": str(f.get("sorte", "") or ""),
            "nikotingehalt": str(f.get("nikotingehalt", "") or ""),
            "price": f.get("regular_price", ""),
            "sonderpreis": f.get("(sonder) PREIS", ""),
            "sonderpreis_min": f.get("(sonder) MinAnzahl", ""),
            "stock_status": f.get("stock_status", ""),
            "stock": f.get("(all) STOCK", f.get("stock", "")),
            "category": f.get("produkt_kategorie", ""),
            "vpe": str(f.get("vpe", "") or ""),
            "url": f.get("product_page_url", ""),
            "sku": f.get("DP-SKU", ""),
            "tags": str(f.get("tags", "") or ""),
            "image_url": self._extract_airtable_image(f),
        }

    @staticmethod
    def _extract_airtable_image(f):
        attachments = f.get("image_attachement", [])
        if attachments and isinstance(attachments, list):
            thumb = attachments[0].get("thumbnails", {})
            large = thumb.get("large", {})
            if large.get("url"):
                return large["url"]
            return attachments[0].get("url", "")
        return ""

    # ------------------------------------------------------------------
    # CSV (letzter Fallback)
    # ------------------------------------------------------------------

    def load_from_csv(self, csv_path):
        import csv
        products = []
        try:
            with open(csv_path, "r", encoding="utf-8-sig") as fh:
                for row in csv.DictReader(fh):
                    products.append(self._normalize_airtable(row))
        except Exception as e:
            log.error(f"CSV-Fehler: {e}")
            return
        with self.lock:
            self.available = [p for p in products if p["stock_status"] == "instock"]
            self.all_products = products
            self._build_indices()
            self.last_loaded = datetime.now()
            self.source = "CSV"
        log.info(f"CSV geladen: {len(self.available)} verfuegbar, {len(self.all_products)} gesamt")
        if _on_cache_loaded:
            _on_cache_loaded()

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_title(title):
        parts = [p.strip() for p in title.split(" - ")]
        brand = parts[0] if len(parts) >= 1 else ""
        product_line = parts[1] if len(parts) >= 2 else ""
        flavor = " - ".join(parts[2:]) if len(parts) >= 3 else ""
        return brand, product_line, flavor

    def _build_indices(self):
        self.brands = set()
        self.categories = set()
        for p in self.all_products:
            if p.get("brand"):
                self.brands.add(p["brand"].lower())
            for cat in p.get("category", "").split("|"):
                cat = cat.strip()
                if cat:
                    self.categories.add(cat.lower())

    def get_brand_list(self):
        return sorted(self.brands)

    def search_available(self, query, max_results=25):
        return self._search(self.available, query, max_results)

    def search_all(self, query, max_results=25):
        return self._search(self.all_products, query, max_results)

    def _search(self, products, query, max_results):
        try:
            from thefuzz import fuzz
            has_fuzzy = True
        except ImportError:
            has_fuzzy = False

        query_lower = query.lower().strip()
        query_parts = query_lower.split()
        scored = []
        for p in products:
            searchable = " ".join([
                p.get("title", "").lower(),
                p.get("category", "").lower(),
                p.get("brand", "").lower(),
                kebab_to_readable(p.get("geschmack", "")).lower(),
                kebab_to_readable(p.get("auswahl", "")).lower(),
                kebab_to_readable(p.get("farbe", "")).lower(),
                p.get("tags", "").lower(),
            ])
            if all(part in searchable for part in query_parts):
                score = sum(1 for part in query_parts if part in p.get("title", "").lower())
                if query_lower == p.get("brand", "").lower():
                    score += 5
                elif query_lower in p.get("brand", "").lower():
                    score += 3
                if all(part in p.get("title", "").lower() for part in query_parts):
                    score += 3
                if "bestseller" in p.get("category", "").lower():
                    score += 10
                scored.append((score, p))

        if not scored and has_fuzzy and len(query_parts) <= 4:
            for p in products:
                searchable = " ".join([
                    p.get("title", "").lower(),
                    p.get("category", "").lower(),
                    p.get("brand", "").lower(),
                    kebab_to_readable(p.get("geschmack", "")).lower(),
                    kebab_to_readable(p.get("auswahl", "")).lower(),
                    kebab_to_readable(p.get("farbe", "")).lower(),
                    p.get("tags", "").lower(),
                ])
                score = fuzz.token_set_ratio(query_lower, searchable)
                if score >= 70:
                    scored.append((score, p))
            scored.sort(key=lambda x: -x[0])
            if scored:
                log.info(f"Fuzzy-Fallback fuer '{query}': {len(scored)} Treffer (top: {scored[0][0]})")

        scored.sort(key=lambda x: -x[0])
        return [p for _, p in scored[:max_results]]

    def get_parents_available(self, brand=None, category=None):
        results = []
        seen_ids = set()

        for p in self.available:
            if p.get("produkt_typ") not in ("variable", "simple", ""):
                continue
            if p.get("post_parent"):
                continue
            if brand and brand.lower() not in p.get("brand", "").lower():
                continue
            if category and category.lower() not in p.get("category", "").lower():
                continue
            results.append(p)
            seen_ids.add(p.get("id"))

        for p in self.all_products:
            pid = p.get("id")
            if pid in seen_ids:
                continue
            if p.get("produkt_typ") != "variable":
                continue
            if p.get("post_parent"):
                continue
            if brand and brand.lower() not in p.get("brand", "").lower():
                continue
            if category and category.lower() not in p.get("category", "").lower():
                continue
            if self.get_variations_available(pid):
                results.append(p)
                seen_ids.add(pid)

        return results

    def get_variations_available(self, parent_id):
        return [p for p in self.available if str(p.get("post_parent")) == str(parent_id)]

    def get_variations_all(self, parent_id):
        return [p for p in self.all_products if str(p.get("post_parent")) == str(parent_id)]

    def is_available(self, product_id):
        return any(p["id"] == str(product_id) for p in self.available)

    def get_product_by_id(self, product_id):
        for p in self.all_products:
            if p["id"] == str(product_id):
                return p
        return None


cache = ProductCache()


def ensure_cache():
    # 1. Cache leer? Erst Snapshot von Disk versuchen (instant statt ~30s API-Load)
    if not cache.all_products:
        cache.load_snapshot()

    # 2. Immer noch leer? Synchron laden (allererster Start, kein Snapshot vorhanden)
    if not cache.all_products:
        try:
            cache.load()
        except Exception as e:
            log.error(f"Cache-Load fehlgeschlagen: {e}")
    # 3. Daten vorhanden aber veraltet? Background-Refresh, alte Daten weiter nutzen
    elif cache.needs_refresh():
        cache.refresh_in_background()

    # 4. Letzter Fallback: CSV
    if not cache.all_products:
        csv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "products.csv")
        if os.path.exists(csv_path):
            log.info("Kein Produkt-Cache - lade CSV Fallback...")
            cache.load_from_csv(csv_path)
