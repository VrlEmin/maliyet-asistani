"""
A101Scraper – A101 market: API ile hızlı ürün arama.

A101'in gerçek API endpoint'ini (https://a101.wawlabs.com/search) kullanarak
JSON response parse eder. iPhone User-Agent kullanarak bot algılamayı önler.

Özellikler:
- JSON API response parse
- Kuruş dönüşümü: Base scraper _safe_price
- URL encoding: Filter parametreleri safe encode edilir
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from src.services.base_scraper import AbstractBaseScraper

logger = logging.getLogger(__name__)

# API endpoint
A101_API_BASE = "https://a101.wawlabs.com"
A101_SEARCH_URL = f"{A101_API_BASE}/search"


class A101Scraper(AbstractBaseScraper):
    """A101 – API ile hızlı ürün arama."""

    MARKET_NAME = "A101"

    async def search_product(self, query: str) -> list[dict[str, Any]]:
        """
        A101 API'sinden ürün arar.
        
        Öncelik sırası:
        1. Cache kontrolü
        2. API isteği (_search_api)
        3. JSON response parse
        4. Sonuçları cache'le
        """
        cache_key = f"search:{query.lower().strip()}"
        cached = await self._get_cached(cache_key)
        if cached:
            logger.info("[A101] Cache hit: %s", query)
            return cached

        results = await self._search_api(query)

        if results:
            await self._set_cache(cache_key, results)
        return results

    async def _search_api(self, query: str) -> list[dict[str, Any]]:
        """
        A101 API'sinden ürün arar.
        
        Parametreler:
        - q: {query}
        - pn: 1
        - rpp: 60
        - filter: available:true
        - filter: locations^location:VS032-SLOT
        """
        results: list[dict[str, Any]] = []

        try:
            # API parametreleri
            # Filter parametreleri aynı key ile birden fazla değer alabilir
            params_list = [
                ("q", query),
                ("pn", "1"),
                ("rpp", "60"),
                ("filter", "available:true"),
                ("filter", "locations^location:VS032-SLOT"),
            ]
            
            # URL encoding (urllib.parse.urlencode() otomatik encode eder)
            query_string = urlencode(params_list)
            full_url = f"{A101_SEARCH_URL}?{query_string}"
            
            # API isteği
            # Header'ları merge et (iPhone User-Agent kullan)
            merged_headers = dict(self.DEFAULT_HEADERS)
            merged_headers.update(
                self.get_headers_for_device(
                    "iphone",
                    referer="https://www.a101.com.tr/",
                    accept="application/json",
                )
            )
            
            # Accept-Encoding header'ını kaldır (httpx otomatik handle eder)
            merged_headers.pop("Accept-Encoding", None)
            
            # API isteği - httpx client'ı direkt kullan
            # Yeni bir client oluştur (DEFAULT_HEADERS sorununu önlemek için)
            import httpx
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
            ) as client:
                response = await client.get(
                    full_url,
                    headers=merged_headers,
                )
                response.raise_for_status()

                # JSON response parse
                # httpx otomatik olarak gzip decompress eder ve encoding'i handle eder
                data = response.json()
            
            # A101 API response formatı: {"res": [{"page_content": [...]}]}
            products = []
            if isinstance(data, dict) and "res" in data:
                res_list = data["res"]
                if res_list and isinstance(res_list, list) and len(res_list) > 0:
                    first_res = res_list[0]
                    if isinstance(first_res, dict) and "page_content" in first_res:
                        products = first_res["page_content"]

            # Ürünleri parse et
            for item in products[:60]:  # rpp=60 kadar
                try:
                    # Ürün adı (A101 API'de "title" kullanılıyor)
                    product_name = item.get("title") or item.get("name") or item.get("product_name")
                    if not product_name or len(product_name) < 3:
                        continue

                    # Fiyat (A101 API'de "price" direkt TL olarak geliyor)
                    raw_price = item.get("price")
                    if raw_price is None:
                        continue
                    
                    try:
                        price = float(raw_price)
                    except (ValueError, TypeError):
                        continue
                    
                    if price <= 0:
                        continue
                    
                    # Kuruş dönüşümü (gerekirse - genelde TL olarak geliyor)
                    price = self._safe_price(price)

                    # Görsel (A101 API'de "image" veya "image_url" liste olarak gelebilir)
                    image_url_raw = item.get("image") or item.get("image_url") or item.get("imageUrl")
                    image_url = None
                    if image_url_raw:
                        if isinstance(image_url_raw, list):
                            # Liste ise, "product" imageType'ına sahip ilk görseli bul
                            for img in image_url_raw:
                                if isinstance(img, dict):
                                    img_type = img.get("imageType", "")
                                    img_url = img.get("url")
                                    if img_type == "product" and img_url:
                                        image_url = img_url
                                        break
                            # Eğer product imageType bulunamazsa, ilk görselin URL'sini al
                            if not image_url and image_url_raw:
                                first_img = image_url_raw[0]
                                if isinstance(first_img, dict):
                                    image_url = first_img.get("url")
                        elif isinstance(image_url_raw, str):
                            # String ise direkt kullan
                            image_url = image_url_raw
                    
                    # URL (A101 API'de "url" veya "link" olabilir)
                    product_url = item.get("url") or item.get("link") or item.get("productUrl")

                    # Ürün ID (A101 API'de "id" kullanılıyor)
                    product_id = str(item.get("id") or item.get("productId") or "")

                    # Gramaj (ürün adından çıkar)
                    gramaj = self._parse_gramaj_from_name(product_name)

                    results.append({
                        "product_name": product_name,
                        "price": price,
                        "currency": "TRY",
                        "image_url": image_url,
                        "market_name": self.MARKET_NAME,
                        "product_id": product_id,
                        "gramaj": gramaj,
                        "url": product_url,  # Ekstra bilgi olarak ekle
                    })

                except Exception as exc:
                    logger.debug("[A101] Ürün parse hatası: %s", exc)
                    continue

        except Exception as exc:
            logger.warning("[A101] API hatası '%s': %s", query, exc)

        return results

    async def get_product_price(self, product_id: str) -> dict[str, Any] | None:
        """Belirli bir ürünün fiyatını getirir (search ile)."""
        found = await self.search_product(product_id)
        return found[0] if found else None

    async def close(self) -> None:
        """A101 scraper kapatılırken parent close."""
        await super().close()
