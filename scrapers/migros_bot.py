"""
MigrosScraper – Migros sanal market API'si üzerinden ürün arama ve fiyat çekme.
"""

from __future__ import annotations

import logging
from typing import Any

from src.services.base_scraper import AbstractBaseScraper

logger = logging.getLogger(__name__)

# Migros API endpoint'leri
MIGROS_API_BASE = "https://www.migros.com.tr/rest/products"
MIGROS_SEARCH_URL = f"{MIGROS_API_BASE}/search"
MIGROS_PRODUCT_URL = f"{MIGROS_API_BASE}/get"


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
                        "price": self._safe_price(raw_price),
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
                "price": self._safe_price(raw_price),
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
