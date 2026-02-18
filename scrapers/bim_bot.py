"""
BimScraper – BİM Birleşik Mağazalar ürün fiyat scraper'ı.

API yaklaşımı:
1. Mobil API (gelecekte network trafiği analizi ile entegre edilecek)
2. Web scraping fallback (aktüel ürünler için)
3. okatalog.com fallback

Not: Mobil API endpoint'leri ve header'ları (X-Device-Id, Authorization) 
network trafiği analizi ile tespit edilecek. Şu an için web scraping kullanılıyor.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from bs4 import BeautifulSoup

from scrapers.base_scraper import AbstractBaseScraper

logger = logging.getLogger(__name__)

# Web scraping için
BIM_BASE_URL = "https://www.bim.com.tr"
BIM_AKTUEL_URL = f"{BIM_BASE_URL}/Categories/100/aktuel-urunler.aspx"

# Mobil API endpoint'leri (gelecekte network trafiği analizi ile güncellenecek)
# TODO: Network trafiği analizi sonrası bu endpoint'ler güncellenecek
BIM_API_BASE = "https://api.bim.com.tr"  # Placeholder - gerçek endpoint tespit edilecek
BIM_MOBILE_SEARCH_URL = f"{BIM_API_BASE}/v1/products/search"  # Placeholder
BIM_MOBILE_STOCK_URL = f"{BIM_API_BASE}/v1/products/stock"  # Placeholder

# okatalog.com – BİM aktüel ürün arşivi (fallback)
OKATALOG_SEARCH_URL = "https://www.okatalog.com"
OKATALOG_BIM_CATEGORIES = [
    "/temel-gida-aktuel-urun-fiyatlari-bim",
    "/sut-urunleri-aktuel-urun-fiyatlari-bim",
    "/atistirmalik-aktuel-urun-fiyatlari-bim",
    "/icecek-aktuel-urun-fiyatlari-bim",
    "/et-urunleri-aktuel-urun-fiyatlari-bim",
    "/temizlik-aktuel-urun-fiyatlari-bim",
]

# Mobil API için header yapısı (gelecekte network trafiği analizi ile güncellenecek)
# TODO: Gerçek header'lar network trafiği analizi ile tespit edilecek
BIM_MOBILE_HEADERS = {
    "X-Device-Id": str(uuid.uuid4()),  # Placeholder - gerçek device ID tespit edilecek
    "Authorization": "Bearer ...",  # Placeholder - gerçek token tespit edilecek
    "User-Agent": "BIM-Online/1.0 (Android; Mobile)",  # Placeholder
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Accept-Language": "tr-TR",
}

# Fiyat dönüşümü için threshold (MigrosScraper benzeri)
BIM_PRICE_DIVISOR = 100.0
_BIM_KURUS_THRESHOLD = 1000.0


def _safe_price(raw: float | int) -> float:
    """
    BİM API fiyatını TL'ye çevirir.
    Fiyat > 1000 ise kuruş kabul edilip 100'e bölünür; aksi hâlde TL'dir.
    MigrosScraper'daki mantıkla aynı.
    """
    raw_float = float(raw)
    if raw_float > _BIM_KURUS_THRESHOLD:
        return round(raw_float / BIM_PRICE_DIVISOR, 2)
    return round(raw_float, 2)


class BimScraper(AbstractBaseScraper):
    """
    BİM – ürün arama ve fiyat çıkarımı.
    
    Strateji:
    1. Mobil API denemesi (şu an devre dışı - endpoint'ler tespit edilecek)
    2. Web scraping (aktüel ürünler)
    3. okatalog.com fallback
    """

    MARKET_NAME = "BIM"

    # ── Ana Arama ─────────────────────────────────────────────────────────────

    async def search_product(self, query: str) -> list[dict[str, Any]]:
        """
        BİM'den ürün arar.
        
        Öncelik sırası:
        1. Mobil API (gelecekte aktif edilecek)
        2. Web scraping (aktüel ürünler)
        3. okatalog.com fallback
        """
        cache_key = f"search:{query.lower().strip()}"
        cached = await self._get_cached(cache_key)
        if cached:
            logger.info("[BIM] Cache hit: %s", query)
            return cached

        results: list[dict[str, Any]] = []

        # 1) Mobil API denemesi (şu an devre dışı - endpoint'ler tespit edilecek)
        # TODO: Network trafiği analizi sonrası aktif edilecek
        # results = await self._search_mobile_api(query)
        # if results:
        #     await self._set_cache(cache_key, results)
        #     return results

        # 2) Web scraping (aktüel ürünler)
        results = await self._search_bim_aktuel(query)

        # 3) okatalog.com fallback
        if not results:
            results = await self._search_okatalog(query)

        if results:
            await self._set_cache(cache_key, results)
        return results

    # ── Mobil API (Gelecekte Aktif Edilecek) ──────────────────────────────────

    async def _search_mobile_api(self, query: str) -> list[dict[str, Any]]:
        """
        BİM mobil API'sinden ürün arar.
        
        NOT: Şu an placeholder. Network trafiği analizi sonrası implement edilecek.
        """
        results: list[dict[str, Any]] = []

        try:
            # Mobil API isteği
            response = await self._make_request(
                BIM_MOBILE_SEARCH_URL,
                method="POST",  # veya GET - network trafiği analizi ile belirlenecek
                json_body={
                    "query": query,
                    "page": 1,
                    "limit": 20,
                },
                headers=BIM_MOBILE_HEADERS,
            )

            # JSON response parse
            data = response.json()
            
            # Response formatı network trafiği analizi ile belirlenecek
            # Tahmini format:
            # {
            #   "data": {
            #     "products": [
            #       {
            #         "id": "12345",
            #         "name": "Ürün Adı",
            #         "price": 1995,  # kuruş veya TL?
            #         "currency": "TRY",
            #         "imageUrl": "https://...",
            #         "stock": true,
            #         "weight": "500g"
            #       }
            #     ]
            #   }
            # }
            
            products = data.get("data", {}).get("products", [])
            if not products:
                products = data.get("products", [])  # Alternatif format

            for item in products[:20]:
                try:
                    raw_price = float(item.get("price", 0))
                    if raw_price <= 0:
                        continue

                    product_info = {
                        "product_name": item.get("name", ""),
                        "price": _safe_price(raw_price),
                        "currency": item.get("currency", "TRY"),
                        "image_url": item.get("imageUrl") or item.get("image_url"),
                        "market_name": self.MARKET_NAME,
                        "product_id": str(item.get("id", "")),
                        "gramaj": _parse_weight_from_api(item.get("weight") or item.get("weightText", "")),
                    }
                    if product_info["product_name"]:
                        results.append(product_info)
                except (ValueError, TypeError) as exc:
                    logger.debug("[BIM] Mobil API ürün parse hatası: %s", exc)
                    continue

        except Exception as exc:
            logger.debug("[BIM] Mobil API hatası (devre dışı): %s", exc)
            # Mobil API başarısız olursa sessizce devam et (fallback'e geç)

        return results

    # ── Web Scraping (Aktüel Ürünler) ────────────────────────────────────────

    async def _search_bim_aktuel(self, query: str) -> list[dict[str, Any]]:
        """bim.com.tr ana sayfasındaki aktüel ürün kartlarını parse eder."""
        results: list[dict[str, Any]] = []

        try:
            response = await self._make_request(
                BIM_BASE_URL,
                headers={"Referer": BIM_BASE_URL},
            )
            text = response.text
            soup = BeautifulSoup(text, "lxml")

            # BİM ana sayfa yapısı: .product class'ı ile ürün kartları
            product_cards = soup.select(".product")

            if not product_cards:
                # Fallback: regex parse
                results = self._regex_parse_bim(text, query.lower())
                if results:
                    return results
                return []

            for card in product_cards:
                try:
                    # Ürün adı: h2.title içinde
                    title_elem = card.select_one("h2.title")
                    if not title_elem:
                        continue
                    
                    product_name = title_elem.get_text(strip=True)
                    if not product_name or len(product_name) < 3:
                        continue

                    # Fiyat: span.curr içinde (format: "14.900,00₺")
                    price_elem = card.select_one("span.curr")
                    if not price_elem:
                        continue
                    
                    # Fiyat metnini al (parent'ından tüm fiyat metnini çek)
                    price_text = price_elem.parent.get_text(strip=True) if price_elem.parent else price_elem.get_text(strip=True)
                    # Format: "14.900,00₺" -> "14900.00"
                    price = _parse_bim_price_from_text(price_text)
                    if not price or price <= 0:
                        continue

                    # Gramaj (ürün adından veya card içinden)
                    gramaj = _parse_gramaj_from_section(card)
                    if not gramaj:
                        gramaj = _parse_gramaj_text(product_name)

                    # Görsel
                    image_url = _extract_image(card)

                    # Ürün ID (link'ten çıkar)
                    link_elem = card.select_one("a[href]")
                    product_id = None
                    if link_elem:
                        href = link_elem.get("href", "")
                        # URL'den ID çıkar (örn: /aktuel-urunler/12345/aktuel.aspx)
                        id_match = re.search(r"/(\d+)/", href)
                        if id_match:
                            product_id = id_match.group(1)

                    results.append({
                        "product_name": product_name.strip(),
                        "price": price,
                        "currency": "TRY",
                        "image_url": image_url,
                        "market_name": self.MARKET_NAME,
                        "product_id": product_id,
                        "gramaj": gramaj,
                    })
                except Exception as exc:
                    logger.debug("[BIM] Ürün parse hatası: %s", exc)
                    continue

            # CSS seçiciler boş kaldıysa regex fallback
            if not results:
                results = self._regex_parse_bim(text, query.lower())

        except Exception as exc:
            logger.warning("[BIM] bim.com.tr arama hatası '%s': %s", query, exc)

        return results

    @staticmethod
    def _regex_parse_bim(text: str, query_lower: str) -> list[dict[str, Any]]:
        """
        bim.com.tr HTML'inden regex ile ürün-fiyat çiftlerini çıkarır.
        Format: "Ürün Adı ... 350,00 ₺" veya "350,\n\n00\n₺"
        """
        results: list[dict[str, Any]] = []
        clean = re.sub(r"\s+", " ", text)

        for m in re.finditer(
            r"(?:##\s*)?([A-ZÇĞİÖŞÜa-zçğıöşü][^\n₺]{5,100}?)\s*"
            r"(\d{1,3}(?:\.\d{3})*,\s*\d{2})\s*₺",
            clean,
        ):
            name = re.sub(r"\s+", " ", m.group(1)).strip()
            price_str = m.group(2).replace(" ", "").replace(".", "").replace(",", ".")
            try:
                price = float(price_str)
            except ValueError:
                continue

            if price <= 0:
                continue

            # Sorgu kelimelerinden en az biri ürün adında geçmeli
            if not any(w in name.lower() for w in query_lower.split() if len(w) >= 3):
                continue

            gramaj = _parse_gramaj_text(name)

            results.append({
                "product_name": name,
                "price": price,
                "currency": "TRY",
                "image_url": None,
                "market_name": "BIM",
                "product_id": None,
                "gramaj": gramaj,
            })
            if len(results) >= 20:
                break

        return results

    # ── okatalog.com Fallback ────────────────────────────────────────────────

    async def _search_okatalog(self, query: str) -> list[dict[str, Any]]:
        """okatalog.com'dan BİM aktüel ürün arşivinden arama."""
        results: list[dict[str, Any]] = []
        query_lower = query.lower()

        for cat_path in OKATALOG_BIM_CATEGORIES:
            if len(results) >= 15:
                break
            try:
                response = await self._make_request(
                    f"{OKATALOG_SEARCH_URL}{cat_path}",
                    headers={"Referer": OKATALOG_SEARCH_URL},
                )
                text = response.text
                soup = BeautifulSoup(text, "lxml")

                cards = soup.select(
                    ".product-card, .urun-card, article, "
                    "[class*='product'], [class*='catalog']"
                )

                for card in cards[:30]:
                    card_text = card.get_text(" ", strip=True)
                    if "BİM" not in card_text.upper():
                        continue

                    # Ürün adı ve fiyat çıkar
                    price_match = re.search(
                        r"Fiyat:\s*(\d+(?:[.,]\d+)?)\s*TL",
                        card_text, re.IGNORECASE,
                    )
                    if not price_match:
                        price_match = re.search(
                            r"(\d+(?:[.,]\d+)?)\s*TL",
                            card_text, re.IGNORECASE,
                        )
                    if not price_match:
                        continue

                    price_str = price_match.group(1).replace(",", ".")
                    try:
                        price = float(price_str)
                    except ValueError:
                        continue

                    # Ürün adını BİM'den sonraki metin olarak al
                    name_match = re.search(
                        r"BİM\s+(.+?)(?:Fiyat|$)",
                        card_text, re.IGNORECASE,
                    )
                    name = name_match.group(1).strip() if name_match else card_text[:100]

                    if not any(w in name.lower() for w in query_lower.split() if len(w) >= 3):
                        continue

                    if price > 0 and name:
                        results.append({
                            "product_name": name,
                            "price": price,
                            "currency": "TRY",
                            "image_url": None,
                            "market_name": self.MARKET_NAME,
                            "product_id": None,
                            "gramaj": _parse_gramaj_text(name),
                        })

            except Exception as exc:
                logger.debug("[BIM] okatalog kategori hatası %s: %s", cat_path, exc)
                continue

        return results

    # ── Diğer ────────────────────────────────────────────────────────────────

    async def get_product_price(self, product_id: str) -> dict[str, Any] | None:
        """Belirli bir ürünün fiyatını getirir (search ile)."""
        found = await self.search_product(product_id)
        return found[0] if found else None


# ═══════════════════════════════════════════════════════════════════════════════
#  Yardımcı Fonksiyonlar
# ═══════════════════════════════════════════════════════════════════════════════


def _parse_bim_price_from_text(price_text: str) -> float | None:
    """
    BİM web scraping fiyat formatını parse eder: "14.900,00₺" -> 14900.00
    Format: binlik ayracı nokta, ondalık ayracı virgül
    """
    if not price_text:
        return None
    
    # "14.900,00₺" formatını temizle
    price_text = price_text.replace("₺", "").replace("TL", "").strip()
    
    # Nokta ve virgül içeriyorsa: "14.900,00"
    if "." in price_text and "," in price_text:
        # Noktaları kaldır (binlik ayraçlar), virgülü noktaya çevir
        price_text = price_text.replace(".", "").replace(",", ".")
        try:
            return float(price_text)
        except ValueError:
            pass
    
    # Sadece virgül varsa: "14900,00"
    if "," in price_text:
        price_text = price_text.replace(",", ".")
        try:
            return float(price_text)
        except ValueError:
            pass
    
    # Sadece sayı varsa
    try:
        return float(re.sub(r"[^\d.]", "", price_text) or 0)
    except ValueError:
        return None


def _parse_weight_from_api(weight_text: str) -> float | None:
    """API'den gelen weight/weightText alanından gramaj çıkarır."""
    if not weight_text:
        return None
    return _parse_gramaj_text(weight_text)


def _parse_gramaj_from_section(section) -> float | None:
    """Bir HTML bölümünden gramaj bilgisini çıkarır."""
    text = section.get_text(" ", strip=True)
    return _parse_gramaj_text(text)


def _parse_gramaj_text(text: str) -> float | None:
    """Metinden gramaj çıkarır (gram veya kg)."""
    if not text:
        return None
    text = text.replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:gr?|gram|g)\b", text, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*kg\b", text, re.I)
    if m:
        return float(m.group(1)) * 1000
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:ml|lt|l)\b", text, re.I)
    if m:
        val = float(m.group(1))
        if val < 20:  # litre
            return val * 1000
        return val  # ml
    return None


def _extract_image(section) -> str | None:
    """Bir HTML bölümünden görsel URL'si çıkarır."""
    img = section.select_one("img")
    if img:
        src = img.get("src") or img.get("data-src") or ""
        if src and not src.startswith("data:"):
            if not src.startswith("http"):
                return BIM_BASE_URL + src
            return src
    return None


def _parse_price(s: str) -> float | None:
    """Fiyat stringini float'a çevirir (genel fallback)."""
    if not s:
        return None
    s = s.replace("TL", "").replace("₺", "").replace(" ", "").strip()
    s = s.replace(",", ".")
    try:
        return float(re.sub(r"[^\d.]", "", s) or 0)
    except (ValueError, TypeError):
        return None
