"""
Airtable Product Cache – loads and indexes products from Airtable.
"""

import os
import requests
from datetime import datetime
from threading import Lock

from dp_connect_bot.config import (
    AIRTABLE_PAT, AIRTABLE_BASE_ID, AIRTABLE_TABLE_ID,
    AIRTABLE_VIEW_AVAILABLE, AIRTABLE_VIEW_ALL,
    CACHE_REFRESH_MINUTES, log,
)
from dp_connect_bot.utils.formatting import kebab_to_readable

# Callback for _build_fuzzy_vocab – set by fuzzy_matching module to avoid circular import
_on_cache_loaded = None


def set_on_cache_loaded(callback):
    """Register a callback that is called after cache.load() finishes."""
    global _on_cache_loaded
    _on_cache_loaded = callback


class ProductCache:
    def __init__(self):
        self.available = []
        self.all_products = []
        self.brands = set()           # Dynamisch aus Daten
        self.categories = set()
        self.last_loaded = None
        self.lock = Lock()

    def needs_refresh(self):
        if not self.last_loaded:
            return True
        return (datetime.now() - self.last_loaded).total_seconds() / 60 > CACHE_REFRESH_MINUTES

    def load(self):
        log.info("Lade Produktdaten aus Airtable...")
        available = self._load_view(AIRTABLE_VIEW_AVAILABLE)
        all_prods = self._load_view(AIRTABLE_VIEW_ALL)

        with self.lock:
            if available is not None:
                self.available = available
            if all_prods is not None:
                self.all_products = all_prods
            self._build_indices()
            self.last_loaded = datetime.now()

        log.info(f"Geladen: {len(self.available)} verfuegbar, {len(self.all_products)} gesamt, {len(self.brands)} Marken")
        if _on_cache_loaded:
            _on_cache_loaded()

    def _load_view(self, view_id):
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
        return [self._normalize(r.get("fields", {})) for r in all_records]

    def _normalize(self, f):
        title = f.get("post_title", "")
        brand, product_line, flavor = self._parse_title(title)
        # Airtable-Felder direkt nutzen wenn vorhanden
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
            "image_url": self._extract_image_url(f),
        }

    @staticmethod
    def _extract_image_url(f):
        """Extrahiert die Thumbnail-URL aus Airtable image_attachement."""
        attachments = f.get("image_attachement", [])
        if attachments and isinstance(attachments, list):
            thumb = attachments[0].get("thumbnails", {})
            # Bevorzuge 'large' fuer gute Qualitaet bei kleiner Dateigroesse
            large = thumb.get("large", {})
            if large.get("url"):
                return large["url"]
            # Fallback: Full-Size URL
            return attachments[0].get("url", "")
        return ""

    @staticmethod
    def _parse_title(title):
        parts = [p.strip() for p in title.split(" - ")]
        brand = parts[0] if len(parts) >= 1 else ""
        product_line = parts[1] if len(parts) >= 2 else ""
        flavor = " - ".join(parts[2:]) if len(parts) >= 3 else ""
        return brand, product_line, flavor

    def _build_indices(self):
        """Baut Marken- und Kategorie-Index aus den echten Daten."""
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
        """Gibt sortierte Liste aller Marken zurueck."""
        return sorted(self.brands)

    def search_available(self, query, max_results=25):
        return self._search(self.available, query, max_results)

    def search_all(self, query, max_results=25):
        return self._search(self.all_products, query, max_results)

    def _search(self, products, query, max_results):
        # Import here to avoid circular import at module level
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
                # Bestseller-Boost
                if "bestseller" in p.get("category", "").lower():
                    score += 10
                scored.append((score, p))

        # Fuzzy-Fallback: Wenn exaktes Matching 0 Treffer bringt, versuche Fuzzy
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
                # Token-Set-Ratio: tolerant bei Wortstellung und Tippfehlern
                score = fuzz.token_set_ratio(query_lower, searchable)
                if score >= 70:
                    scored.append((score, p))
            scored.sort(key=lambda x: -x[0])
            if scored:
                log.info(f"Fuzzy-Fallback fuer '{query}': {len(scored)} Treffer (top: {scored[0][0]})")

        scored.sort(key=lambda x: -x[0])
        return [p for _, p in scored[:max_results]]

    def get_parents_available(self, brand=None, category=None):
        """Alle verfuegbaren Parent-Produkte, optional gefiltert.
        Enthaelt auch variable Parents mit stock=0 wenn sie verfuegbare Variationen haben."""
        results = []
        seen_ids = set()

        # Erst aus available-Liste
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

        # Dann variable Parents aus all_products die verfuegbare Variationen haben
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
            # Nur aufnehmen wenn es verfuegbare Variationen gibt
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

    def load_from_csv(self, csv_path):
        import csv
        products = []
        try:
            with open(csv_path, "r", encoding="utf-8-sig") as fh:
                for row in csv.DictReader(fh):
                    products.append(self._normalize(row))
        except Exception as e:
            log.error(f"CSV-Fehler: {e}")
            return
        with self.lock:
            self.available = [p for p in products if p["stock_status"] == "instock"]
            self.all_products = products
            self._build_indices()
            self.last_loaded = datetime.now()
        log.info(f"CSV geladen: {len(self.available)} verfuegbar, {len(self.all_products)} gesamt")
        if _on_cache_loaded:
            _on_cache_loaded()


cache = ProductCache()


def ensure_cache():
    if cache.needs_refresh():
        try:
            cache.load()
        except Exception as e:
            log.error(f"Cache-Refresh fehlgeschlagen: {e}")
    if not cache.all_products:
        csv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "products.csv")
        if os.path.exists(csv_path):
            log.info("Airtable leer/down - lade CSV Fallback...")
            cache.load_from_csv(csv_path)
