"""
MigrosScraper – Migros sanal market API'si üzerinden ürün arama ve fiyat çekme.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from scrapers.base_scraper import AbstractBaseScraper

logger = logging.getLogger(__name__)

# Migros API endpoint'leri
MIGROS_API_BASE = "https://www.migros.com.tr/rest/products"
MIGROS_SEARCH_URL = f"{MIGROS_API_BASE}/search"
MIGROS_PRODUCT_URL = f"{MIGROS_API_BASE}/get"

# API fiyatları kuruş (1/100 TL) olarak geliyor; TL'ye çeviriyoruz
MIGROS_PRICE_DIVISOR = 100.0

# Savunma eşiği: Bu değerden büyükse kuruş olarak gelmiş demektir
_KURUS_THRESHOLD = 1000.0


def _safe_price(raw: float) -> float:
    """
    Migros fiyatını TL'ye çevirir.
    Fiyat > 1000 ise kuruş kabul edilip 100'e bölünür; aksi hâlde TL'dir.
    Böylece cache'te kalan eski kuruş verileri de doğru işlenir.
    """
    if raw > _KURUS_THRESHOLD:
        return round(raw / MIGROS_PRICE_DIVISOR, 2)
    return round(raw, 2)


class MigrosScraper(AbstractBaseScraper):
    """Migros sanal market API scraper'ı."""

    MARKET_NAME = "Migros"

    async def search_product(self, query: str) -> list[dict[str, Any]]:
        """Migros API üzerinden ürün arar."""

        # Önce cache'e bak
        cache_key = f"search:{query.lower().strip()}"
        cached = await self._get_cached(cache_key)
        if cached:
            logger.info("[Migros] Cache hit: %s", query)
            return cached

        results: list[dict[str, Any]] = []

        try:
            response = await self._make_request(
                MIGROS_SEARCH_URL,
                params={
                    "q": query,
                    "sayfa": 1,
                    "sirpiing-piinlama": "onpiine",
                },
                headers={
                    "Accept": "application/json",
                    "Referer": "https://www.migros.com.tr/",
                },
            )

            data = response.json()
            products = data.get("data", {}).get("storeProductInfos", [])

            for item in products[:20]:  # İlk 20 sonuç
                try:
                    raw_price = float(item.get("shownPrice", 0))
                    product_name = item.get("name", "")
                    product_info = {
                        "product_name": product_name,
                        "price": _safe_price(raw_price),
                        "currency": "TRY",
                        "image_url": item.get("imageUrl"),
                        "market_name": self.MARKET_NAME,
                        "product_id": str(item.get("id", "")),
                        "barcode": item.get("barcode"),
                        "category": item.get("categoryName"),
                        "gramaj": self._parse_gramaj_from_name(product_name),
                    }
                    if product_info["price"] > 0:
                        results.append(product_info)
                except (ValueError, TypeError) as exc:
                    logger.debug("[Migros] Ürün parse hatası: %s", exc)
                    continue

        except Exception as exc:
            logger.error("[Migros] Arama hatası '%s': %s", query, exc)

        # Sonuçları cache'le
        if results:
            await self._set_cache(cache_key, results)

        return results

    async def get_product_price(self, product_id: str) -> dict[str, Any] | None:
        """Belirli bir Migros ürününün güncel fiyatını getirir."""

        cache_key = f"product:{product_id}"
        cached = await self._get_cached(cache_key)
        if cached:
            return cached

        try:
            response = await self._make_request(
                MIGROS_PRODUCT_URL,
                params={"id": product_id},
                headers={
                    "Accept": "application/json",
                    "Referer": "https://www.migros.com.tr/",
                },
            )

            data = response.json()
            item = data.get("data", {})
            raw_price = float(item.get("shownPrice", 0))

            result = {
                "product_name": item.get("name", ""),
                "price": _safe_price(raw_price),
                "currency": "TRY",
                "image_url": item.get("imageUrl"),
                "market_name": self.MARKET_NAME,
                "product_id": product_id,
            }

            if result["price"] > 0:
                await self._set_cache(cache_key, result, ttl=1800)
                return result

        except Exception as exc:
            logger.error("[Migros] Fiyat çekme hatası (id=%s): %s", product_id, exc)

        return None

    @staticmethod
    def _parse_gramaj_from_name(product_name: str) -> float | None:
        """Ürün adından gramaj bilgisini çıkarır."""
        if not product_name:
            return None
        
        product_name = product_name.replace(",", ".")
        
        # Gram (örn: "1000 G", "750 G")
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:gr?|gram|g)\b", product_name, re.I)
        if m:
            return float(m.group(1))
        
        # Kilogram (örn: "2 Kg", "1.5 Kg")
        m = re.search(r"(\d+(?:\.\d+)?)\s*kg\b", product_name, re.I)
        if m:
            return float(m.group(1)) * 1000
        
        # Mililitre/Litre (örn: "500 ml", "1 L")
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:ml|lt|l)\b", product_name, re.I)
        if m:
            val = float(m.group(1))
            if val < 20:  # litre
                return val * 1000
            return val  # ml
        
        return None
