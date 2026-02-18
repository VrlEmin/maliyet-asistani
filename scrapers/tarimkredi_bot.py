"""
TarimKrediScraper – Tarım Kredi Kooperatifi Marketleri ürün scraper'ı.

Tarım Kredi'nin kurumsal sitesi (tkkoop.com.tr) üzerinden ürün verileri çekilir.
BeautifulSoup ile HTML scraping yaparak ürün bilgilerini parse eder.

Kaynak: https://www.tkkoop.com.tr/arama?ara={query}
"""

from __future__ import annotations

import html as html_module
import logging
import re
from typing import Any
from urllib.parse import quote

from bs4 import BeautifulSoup

from scrapers.base_scraper import AbstractBaseScraper

logger = logging.getLogger(__name__)

# Arama URL'si – Sunucu taraflı (Server-Side) sonuç döner
TKKOOP_BASE_URL = "https://www.tkkoop.com.tr"
TKKOOP_SEARCH_URL = f"{TKKOOP_BASE_URL}/arama"
TKKOOP_KAMPANYA_URL = f"{TKKOOP_BASE_URL}/haftalik-kampanyalar"

# iPhone User-Agent – Mutlaka bu User-Agent kullanılır (bot algılamayı önlemek için)
TKKOOP_IPHONE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.6 Mobile/15E148 Safari/604.1"
)

# API header'ları (minimal - Accept-Encoding kaldırıldı performans için)
TKKOOP_HEADERS = {
    "User-Agent": TKKOOP_IPHONE_USER_AGENT,
    "Referer": TKKOOP_BASE_URL,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "tr-TR,tr;q=0.9",
}

# Kategori haritalama: Anahtar kelimeler → kategori URL'leri
CATEGORY_MAP: dict[str, str] = {
    "tavuk": f"{TKKOOP_BASE_URL}/et-tavuk-sarkuteri/tavuk-eti",
    "piliç": f"{TKKOOP_BASE_URL}/et-tavuk-sarkuteri/tavuk-eti",
    "kanat": f"{TKKOOP_BASE_URL}/et-tavuk-sarkuteri/tavuk-eti",
    "bonfile": f"{TKKOOP_BASE_URL}/et-tavuk-sarkuteri/beyaz-et",  # Bonfile ürünleri beyaz et kategorisinde
    "beyaz et": f"{TKKOOP_BASE_URL}/et-tavuk-sarkuteri/beyaz-et",
    "süt": f"{TKKOOP_BASE_URL}/sut-ve-sut-urunleri",
    "peynir": f"{TKKOOP_BASE_URL}/sut-ve-sut-urunleri",
    "yoğurt": f"{TKKOOP_BASE_URL}/sut-ve-sut-urunleri",
    "et": f"{TKKOOP_BASE_URL}/et-tavuk-sarkuteri/et-urunleri",
    "dana": f"{TKKOOP_BASE_URL}/et-tavuk-sarkuteri/et-urunleri",
    "kıyma": f"{TKKOOP_BASE_URL}/et-tavuk-sarkuteri/et-urunleri",
}

# Güvenli markalar: Bu markalar geçiyorsa negatif filtre esnetilir
SAFE_BRANDS: list[str] = ["banvit", "erpiliç", "şenpiliç", "erp", "besler", "gedik"]

# Negatif filtreler: Arama terimi → hariç tutulacak kelimeler
NEGATIVE_FILTERS: dict[str, list[str]] = {
    "tavuk": [
        "dana", "sığır", "kuzu", "koyun", "et ",  # Diğer et türleri
        "çorba", "noodle", "aromalı", "yumurta", "pilav", "sote", "suyu",  # Hazır yemek/çorba
        "hazır", "bulyon", "barda", "şehriye", "erişte", "makarna",  # Hazır gıda
        "gezen tavuk yumurta", "yumurta",  # Yumurta ürünleri
    ],
    "piliç": [
        "dana", "sığır", "kuzu", "koyun", "et ",
        "çorba", "noodle", "aromalı", "yumurta", "pilav", "sote", "suyu",
        "hazır", "bulyon", "barda", "şehriye", "erişte", "makarna",
    ],
    "kanat": [
        "dana", "sığır", "kuzu", "koyun",
        "çorba", "noodle", "aromalı", "yumurta", "pilav", "sote", "suyu",
        "hazır", "bulyon", "barda", "şehriye", "erişte", "makarna",
    ],
}


class TarimKrediScraper(AbstractBaseScraper):
    """Tarım Kredi Kooperatifi – tkkoop.com.tr üzerinden HTML scraping ile ürün arama."""

    MARKET_NAME = "TarimKredi"

    @staticmethod
    def _map_query_to_category(query: str) -> list[str]:
        """Sorguyu analiz edip kategori URL'lerine yönlendirir. Kategori URL'leri 404 döndüğü için şu an devre dışı."""
        # Kategori URL'leri 404 döndüğü için doğrudan arama yapıyoruz
        return []

    @staticmethod
    def _should_filter_product(product_name: str, search_query: str | None) -> bool:
        """
        Ürün adına göre negatif filtre uygular.
        "tavuk" arandığında sadece beyaz et kategorisinden ürünler geçer.
        """
        if not search_query or not product_name:
            return False
        
        q_lower = search_query.lower()
        name_lower = product_name.lower()
        
        # "tavuk" arandığında özel kontrol
        if "tavuk" in q_lower:
            # Önce yumurta kontrolü: "yumurta" kelimesi varsa ve "gezen tavuk yumurta" gibi bir ifade varsa filtrele
            if "yumurta" in name_lower:
                # "gezen tavuk yumurta" gibi ifadeler filtrelenmeli
                if "gezen tavuk yumurta" in name_lower or ("gezen" in name_lower and "yumurta" in name_lower):
                    return True
            
            # Diğer alakasız ürünleri kontrol et (çorba, noodle, pilav, sote, suyu vb.)
            exclude_keywords = [
                "çorba", "noodle", "aromalı", "pilav", "sote", "suyu",
                "hazır", "bulyon", "barda", "şehriye", "erişte", "makarna",
            ]
            # Eğer alakasız kelime varsa filtrele (gerçek tavuk eti ifadesi yoksa)
            if any(exclude in name_lower for exclude in exclude_keywords):
                # Gerçek tavuk eti ifadeleri kontrolü: bu ifadeler varsa filtreleme
                tavuk_eti_keywords = [
                    "tavuk eti", "tavuk göğsü", "tavuk göğüsü", "tavuk bonfile", "tavuk but",
                    "tavuk kanat", "tavuk baget", "piliç", "piliç eti", "piliç göğsü", "piliç bonfile",
                    "tavuk fileto", "tavuk şinitzel", "tavuk schnitzel", "tavuk nugget", "tavuk cordon", 
                    "tavuk gövde", "gövde tavuk", "tavuk sarkuteri", "tavuk salam", "tavuk sosis", "tavuk sucuk",
                    "gezen tavuk", "köytav",  # Gezen tavuk eti ürünleri (yumurta hariç)
                ]
                # Gerçek tavuk eti ifadesi varsa geçer
                if any(keyword in name_lower for keyword in tavuk_eti_keywords):
                    return False
                # Gerçek tavuk eti ifadesi yoksa ve alakasız kelime varsa filtrele
                return True
            
            # Alakasız kelime yoksa, gerçek tavuk eti ifadesi kontrolü
            tavuk_eti_keywords = [
                "tavuk eti", "tavuk göğsü", "tavuk göğüsü", "tavuk bonfile", "tavuk but",
                "tavuk kanat", "tavuk baget", "piliç", "piliç eti", "piliç göğsü", "piliç bonfile",
                "tavuk fileto", "tavuk şinitzel", "tavuk schnitzel", "tavuk nugget", "tavuk cordon", 
                "tavuk gövde", "gövde tavuk", "tavuk sarkuteri", "tavuk salam", "tavuk sosis", "tavuk sucuk",
                "gezen tavuk", "köytav",
            ]
            # Gerçek tavuk eti ifadesi varsa geçer
            if any(keyword in name_lower for keyword in tavuk_eti_keywords):
                return False
            # Gerçek tavuk eti ifadesi yoksa ve "tavuk" kelimesi geçiyorsa filtrele (belirsiz ürünler)
            if "tavuk" in name_lower:
                return True
        
        # Güvenli marka kontrolü: Marka geçiyorsa filtre uygulanmaz (örn. ERP TAVUK NUGGET geçer)
        if any(brand in name_lower for brand in SAFE_BRANDS):
            return False
        
        # Diğer negatif filtre kontrolü
        for keyword, exclude_words in NEGATIVE_FILTERS.items():
            if keyword in q_lower:
                if any(exclude in name_lower for exclude in exclude_words):
                    return True
        
        return False

    async def search_product(self, query: str) -> list[dict[str, Any]]:
        """
        tkkoop.com.tr sitesinden ürün arar.
        
        Öncelik sırası:
        1. Cache kontrolü
        2. tkkoop.com.tr URL'ine istek (/arama?ara={query})
        3. HTML response parse
        4. Sonuçları cache'le
        """
        cache_key = f"search:{query.lower().strip()}"
        cached = await self._get_cached(cache_key)
        if cached:
            logger.info("[TarimKredi] Cache hit: %s", query)
            return cached

        results = await self._search_tkkoop(query)

        if results:
            await self._set_cache(cache_key, results)
        return results

    async def _search_tkkoop(self, query: str) -> list[dict[str, Any]]:
        """
        tkkoop.com.tr sitesinden ürün arar.
        Esnek Arama: Birden fazla kelime aratılıp sonuç gelmezse anahtar kelimeler parçalanıp tekrar aranır.
        """
        import httpx

        results: list[dict[str, Any]] = []
        merged_headers = dict(self.DEFAULT_HEADERS)
        merged_headers.update(TKKOOP_HEADERS)
        merged_headers.pop("Accept-Encoding", None)
        seen_keys: set[tuple[str, str]] = set()

        def dedupe_add(prods: list[dict[str, Any]]) -> None:
            for p in prods:
                key = (p.get("product_name") or "", p.get("market_name") or "")
                if key not in seen_keys:
                    seen_keys.add(key)
                    results.append(p)

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
            ) as client:
                # Ana sorgu araması (sadece tek bir arama)
                search_url = f"{TKKOOP_SEARCH_URL}?ara={quote(query, safe='')}"
                logger.info("[TarimKredi] Arama: '%s'", query)
                response = await client.get(search_url, headers=merged_headers)
                response.raise_for_status()
                html_text = response.text
                results_part = self._parse_tkkoop_html(html_text, search_query=query)
                dedupe_add(results_part)

        except Exception as exc:
            logger.warning("[TarimKredi] Arama hatası '%s': %s", query, exc)

        return results[:20]  # Maksimum 20 ürün döndür (performans için)

    def _parse_tkkoop_html(self, html: str, search_query: str | None = None) -> list[dict[str, Any]]:
        """
        tkkoop.com.tr HTML sayfasından ürün listesi çıkarır.
        Öncelik: Link tabanlı parsing → Ürün görsellerinden kart.
        """
        results: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        def add_unique(p: dict[str, Any]) -> bool:
            name = (p.get("product_name") or "").strip()
            if not name or name in seen_names:
                return False
            seen_names.add(name)
            results.append(p)
            return True

        try:
            q_lower = (search_query or "").lower().split() if search_query else []

            # BeautifulSoup'u sadece bir kez parse et (performans optimizasyonu)
            soup = BeautifulSoup(html, "lxml")
            
            # 1. Link tabanlı: href'inde /urun/ veya /urun-detay/ geçen <a> (en hızlı ve güvenilir)
            for a in soup.find_all("a", href=True, limit=50):
                href = (a.get("href") or "").strip()
                if "/urun/" in href or "/urun-detay/" in href:
                    product = self._parse_tkkoop_card(a, search_query=search_query)
                    if product and add_unique(product):
                        if len(results) >= 20:  # Early exit: İlk 20 ürün bulunduğunda durdur
                            return results

            # 2. Ürün görsellerinden kart (sadece yeterli sonuç yoksa)
            if len(results) < 20:
                for img in soup.select('img[src*="/assets/images/urun/"]')[:30]:
                    img_src = img.get("src", "")
                    if not img_src:
                        continue
                    parent = img.find_parent("a") or img.find_parent("div") or img.find_parent()
                    if parent:
                        product = self._parse_tkkoop_card(parent, img_src, search_query=search_query)
                        if product and add_unique(product):
                            if len(results) >= 20:  # Early exit
                                return results
            

        except Exception as exc:
            logger.warning("[TarimKredi] HTML parse hatası: %s", exc)

        return results

    def _parse_tkkoop_card(self, card, img_src: str | None = None, search_query: str | None = None) -> dict[str, Any] | None:
        """
        Tek bir ürün kartını parse eder.
        Ürün adı: img alt öncelikli; fiyat: birleşik 279,00TL yapısı dahil tüm span/metin taranır.
        Negatif filtre: search_query'ye göre alakasız ürünler filtrelenir.
        """
        try:
            # Görsel URL
            image_url = img_src
            if not image_url:
                img_el = card.select_one("img")
                if img_el:
                    image_url = img_el.get("src") or img_el.get("data-src") or img_el.get("data-lazy-src")

            # Ürün adı – Resimden isim: img alt attribute'unu önceliklendir (Tarım Kredi'de isim bazen sadece orada)
            product_name_raw = None
            img_el = card.select_one("img")
            if img_el:
                alt = (img_el.get("alt") or "").strip()
                if len(alt) > 2:
                    product_name_raw = alt
            if not product_name_raw:
                for name_sel in (
                    "h2, h3, h4, h5",
                    ".urun-adi, .product-name, [class*='name'], [class*='adi'], [class*='title'], [class*='baslik']",
                    "a, strong, b",
                ):
                    name_el = card.select_one(name_sel)
                    if name_el:
                        product_name_raw = name_el.get_text(strip=True)
                        if product_name_raw and len(product_name_raw) > 2:
                            break
            if not product_name_raw:
                product_name_raw = card.get_text(strip=True)

            # Fallback: görsel dosya adından isim (tk-jersey-sut_1768977230.png -> TK JERSEY SÜT)
            if (not product_name_raw or len(product_name_raw) < 3) and image_url:
                import os
                img_filename = os.path.basename(image_url)
                if "_" in img_filename:
                    product_name_raw = img_filename.split("_")[0].replace("-", " ").replace("_", " ").upper()
                else:
                    product_name_raw = img_filename.replace(".png", "").replace(".jpg", "").replace("-", " ").upper()

            if not product_name_raw or len(product_name_raw) < 3:
                return None

            product_name = html_module.unescape(product_name_raw).strip()

            # Early Exit: Ürün adını çektikten sonra hemen filtre kontrolü yap
            # Eğer ürün filtrelenecekse fiyat parse etme adımına geçme (performans optimizasyonu)
            if self._should_filter_product(product_name, search_query):
                logger.debug("[TarimKredi] Ürün filtrelendi (early exit): '%s' (sorgu: '%s')", product_name[:50], search_query)
                return None

            # Fiyat: birleşik "279,00TL" veya "37,50 TL" – regex ile sayı+virgül+TL yakala (ürün adı + kart metni)
            price = 0.0
            # Kartın tüm metninde ve span'larda birleşik fiyat ara (gelişmiş yöntem)
            card_text = card.get_text()
            price_text = _extract_price_from_text_enhanced(product_name, card)
            if not price_text:
                price_text = _extract_price_from_text_enhanced(card_text, card)
            if not price_text:
                for span in card.select("span"):
                    price_text = _extract_price_from_text(span.get_text())
                    if price_text:
                        break
            if price_text:
                parsed = _parse_price(price_text)
                if parsed and parsed > 0:
                    price = parsed
                # Ürün adından fiyatı temizle (TK JERSEY SÜT279,00TL -> TK JERSEY SÜT)
                product_name = _strip_price_from_name(product_name)

            # Görsel URL mutlak yap
            if image_url and not image_url.startswith("http"):
                image_url = f"{TKKOOP_BASE_URL}{image_url}" if image_url.startswith("/") else f"{TKKOOP_BASE_URL}/{image_url}"

            # Ürün URL – card zaten <a> ise href, değilse içindeki a[href]
            product_url = None
            if card.name == "a" and card.get("href"):
                href = card.get("href", "").strip()
            else:
                link_el = card.select_one("a[href]")
                href = (link_el.get("href", "") if link_el else "").strip()
            if href:
                product_url = href if href.startswith("http") else (f"{TKKOOP_BASE_URL}{href}" if href.startswith("/") else f"{TKKOOP_BASE_URL}/{href}")

            product_id = ""
            if product_url:
                product_id = product_url.rstrip("/").split("/")[-1] or ""
            else:
                pid = card.get("data-product-id") or card.get("data-id") or card.get("id")
                if pid is not None:
                    product_id = str(pid)

            gramaj = self._parse_gramaj_from_name(product_name)

            # NOT: Filtre kontrolü zaten ürün adı çekildikten hemen sonra yapıldı (early exit)

            return {
                "product_name": product_name,
                "price": price,
                "currency": "TRY",
                "image_url": image_url,
                "market_name": self.MARKET_NAME,
                "product_id": product_id,
                "gramaj": gramaj,
                "url": product_url,
            }

        except Exception as exc:
            logger.debug("[TarimKredi] Ürün kartı parse hatası: %s", exc)
            return None

    @staticmethod
    def _parse_gramaj_from_name(product_name: str) -> float | None:
        """Ürün adından gramaj bilgisini çıkarır."""
        if not product_name:
            return None
        
        product_name = product_name.replace(",", ".")
        
        # Gram
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:gr?|gram|g)\b", product_name, re.I)
        if m:
            return float(m.group(1))
        
        # Kilogram
        m = re.search(r"(\d+(?:\.\d+)?)\s*kg\b", product_name, re.I)
        if m:
            return float(m.group(1)) * 1000
        
        # Mililitre/Litre
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:ml|lt|l)\b", product_name, re.I)
        if m:
            val = float(m.group(1))
            if val < 20:  # litre
                return val * 1000
            return val  # ml
        
        return None

    async def get_product_price(self, product_id: str) -> dict[str, Any] | None:
        """Belirli bir ürünün fiyatını getirir (search ile)."""
        found = await self.search_product(product_id)
        return found[0] if found else None

    async def close(self) -> None:
        """Tarım Kredi scraper kapatılırken parent close."""
        await super().close()


# ═══════════════════════════════════════════════════════════════════════════════
#  Yardımcı Fonksiyonlar
# ═══════════════════════════════════════════════════════════════════════════════

# Birleşik fiyat: 279,00TL veya 37,50 TL (sayı + virgül/nokta + TL)
_PRICE_PATTERN = re.compile(r"(\d{1,6}[.,]\d{1,2})\s*TL", re.I)

def _extract_price_from_text(s: str) -> str | None:
    """Metinden 'sayı,sayıTL' veya 'sayı,sayı TL' formatında fiyat çıkarır."""
    if not s:
        return None
    m = _PRICE_PATTERN.search(s)
    return m.group(1) if m else None


def _extract_price_from_text_enhanced(s: str, card_element=None) -> str | None:
    """Gelişmiş fiyat çıkarma: parçalanmış span'ları da yakalar."""
    # Mevcut pattern ile dene
    price_text = _extract_price_from_text(s)
    if price_text:
        return price_text
    
    # Parçalanmış format: "279" + "," + "00" + "TL" (farklı span'larda)
    if card_element:
        # Tüm span'ları tara ve birleştir
        spans = card_element.select("span")
        combined = " ".join([span.get_text(strip=True) for span in spans])
        price_text = _extract_price_from_text(combined)
        if price_text:
            return price_text
    
    return None


def _strip_price_from_name(name: str) -> str:
    """Ürün adından birleşik fiyat ifadesini (279,00TL vb.) temizler."""
    if not name:
        return name
    # Harf+sayı+virgül+TL veya sadece sayı+TL kısmını kes
    name = re.sub(r"\d+[.,]\d+\s*TL\s*$", "", name, flags=re.I).strip()
    name = re.sub(r"([A-ZÇĞIİÖŞÜa-zçğıöşü])(\d+[.,]\d+)\s*TL", r"\1", name, flags=re.I).strip()
    return name.strip()


def _parse_price(s: str) -> float | None:
    """
    Fiyat stringini float'a çevirir.
    Türkçe format: "1.234,56" → 1234.56
    Düz format: "199.00" → 199.00
    """
    if not s:
        return None
    
    # HTML karakterlerini temizle
    s = html_module.unescape(s)
    s = s.replace("TL", "").replace("₺", "").replace(" ", "").strip()

    # Türkçe format: nokta binlik, virgül ondalık (1.819,00 → 1819.00)
    # Önce sayıları ve ayırıcıları koru
    if "," in s and "." in s:
        # Hem nokta hem virgül var: nokta binlik, virgül ondalık
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        # Sadece virgül var: ondalık ayırıcı
        s = s.replace(",", ".")
    # Nokta tek ve sonda ise ondalık; birden fazla nokta varsa binlik
    elif s.count(".") > 1:
        s = s.replace(".", "")

    try:
        # Sadece sayıları ve noktayı koru
        cleaned = re.sub(r"[^\d.]", "", s)
        if not cleaned:
            return None
        return float(cleaned)
    except (ValueError, TypeError):
        return None
