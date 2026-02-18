"""
TekelScraper – karekod.org blog sayfalarından alkol ve sigara fiyatlarını çeken scraper.

karekod.org blog yazılarındaki tablo formatındaki fiyat listelerini parse eder.
BeautifulSoup ile HTML scraping yaparak ürün bilgilerini çıkarır.

Kaynaklar:
- Alkol: https://www.karekod.org/blog/alkol-fiyatlari/
- Sigara: https://www.karekod.org/blog/sigara-fiyatlari-2026/
"""

from __future__ import annotations

import html as html_module
import logging
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from scrapers.base_scraper import AbstractBaseScraper

logger = logging.getLogger(__name__)

# URL'ler
KAREKOD_ALCOHOL_URL = "https://www.karekod.org/blog/alkol-fiyatlari/"
KAREKOD_CIGARETTE_URL = "https://www.karekod.org/blog/sigara-fiyatlari-2026/"

# Cache TTL: 12 saat (43200 saniye)
TEKEL_CACHE_TTL = 43200


def _parse_price(s: str) -> float | None:
    """
    Fiyat stringini float'a çevirir.
    Format: "1.175 TL" → 1175.0
    Türkçe format desteği: "1.234,56" → 1234.56
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


def _normalize_product_name(name: str) -> str:
    """
    Ürün adındaki hacim bilgilerini ve gereksiz karakterleri temizler.
    
    Örnekler:
    - "Efes Pilsen 50 cl" → "Efes Pilsen"
    - "Rakı 70'lik" → "Rakı"
    - "Viski 750 ml" → "Viski"
    """
    if not name:
        return ""
    
    # Unicode normalize
    import unicodedata
    normalized = unicodedata.normalize("NFKD", name)
    
    # Hacim bilgilerini temizle (regex ile)
    # 70'lik, 50 cl, 750 ml, 1 L, 0.5 L gibi ifadeleri kaldır
    volume_patterns = [
        r"\d+['']?lik",  # 70'lik, 70lik
        r"\d+(?:\.\d+)?\s*(?:cl|ml|lt|l|L)",  # 50 cl, 750 ml, 1 L
        r"\d+(?:\.\d+)?\s*(?:litre|litre)",  # 1 litre
    ]
    
    cleaned = normalized
    for pattern in volume_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    
    # Fazla boşlukları temizle
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    
    return cleaned


class TekelScraper(AbstractBaseScraper):
    """karekod.org blog sayfalarından alkol ve sigara fiyatlarını çeken scraper."""

    MARKET_NAME = "Tekel"
    
    # Cache TTL override: 12 saat
    CACHE_TTL = TEKEL_CACHE_TTL
    
    async def _get_client(self) -> httpx.AsyncClient:
        """httpx client'ı Accept-Encoding olmadan oluştur (karekod.org bot algılaması için)."""
        if self._client is None or self._client.is_closed:
            # Accept-Encoding header'ını kaldır (gzip/brotli sorunları için)
            headers = dict(self.DEFAULT_HEADERS)
            headers.pop("Accept-Encoding", None)
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
                headers=headers,
            )
        return self._client

    def _parse_text_prices(self, soup: BeautifulSoup) -> list[dict[str, Any]]:
        """
        HTML içindeki metin formatındaki fiyatları parse eder.
        Format: "Parliament Night Blue / Aqua Blue / Reserve: 100 TL"
        
        Returns:
            Ürün listesi
        """
        results: list[dict[str, Any]] = []
        
        try:
            # Tüm metni al
            text = soup.get_text()
            
            # Fiyat pattern'i: "Ürün Adı: Fiyat TL" veya "Ürün Adı – Fiyat TL"
            # Örnek: "Parliament Night Blue / Aqua Blue / Reserve: 100 TL"
            # Daha spesifik pattern: Marka adı ile başlayan, ":" veya "–" ile biten, sonra fiyat
            price_patterns = [
                # Format: "Parliament Night Blue / Aqua Blue / Reserve: 100 TL"
                r"([A-Z][A-Za-z\s]+(?:/[A-Za-z\s]+)*)\s*[:–]\s*(\d+(?:\.\d+)?)\s*TL",
                # Format: "Parliament Night Blue Pack / Long (Uzun): 105 TL"
                r"([A-Z][A-Za-z\s()]+(?:/[A-Za-z\s()]+)*)\s*[:–]\s*(\d+(?:\.\d+)?)\s*TL",
            ]
            
            seen_products = set()
            
            for pattern in price_patterns:
                matches = re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE)
                for match in matches:
                    product_name_raw = match.group(1).strip()
                    price_text = match.group(2).strip()
                    
                    # Tütün ürünlerini atla (GR, kg, gram gibi ifadeler içeriyorsa)
                    if re.search(r'\d+\s*(?:GR|gram|kg|kilogram|sarmalık)', product_name_raw, re.IGNORECASE):
                        continue
                    
                    # Başlık/metin karışıklığını önle (çok uzun veya çok kısa ürün adlarını atla)
                    if len(product_name_raw) < 5 or len(product_name_raw) > 100:
                        continue
                    
                    # Fiyatı parse et
                    price = _parse_price(price_text)
                    if price is None or price <= 0:
                        continue
                    
                    # Ürün adını temizle ve normalize et
                    # Başlık/metin karışıklığını önle
                    product_name = product_name_raw.strip()
                    # Fazla boşlukları temizle
                    product_name = re.sub(r'\s+', ' ', product_name)
                    # Başlık kelimelerini temizle (başta ve sonda)
                    product_name = re.sub(r'^(Sigara Fiyatları|Fiyat Listesi|Fiyatı|Fiyat|Philip Morris|JTI|BAT|Imperial Tobacco)\s*', '', product_name, flags=re.IGNORECASE)
                    product_name = re.sub(r'\s*(Sigara Fiyatları|Fiyat Listesi|Fiyatı|Fiyat)$', '', product_name, flags=re.IGNORECASE)
                    # Başlık metinlerini temizle (örn: "Philip Morris Sigara FiyatlarıParliament Night Blue")
                    # "Sigara Fiyatları" kelimesini her yerden kaldır
                    product_name = re.sub(r'Sigara\s+Fiyatları', '', product_name, flags=re.IGNORECASE)
                    product_name = re.sub(r'Fiyat\s+Listesi', '', product_name, flags=re.IGNORECASE)
                    # Şirket isimlerini kaldır (başta)
                    product_name = re.sub(r'^(Philip Morris|JTI|BAT|Imperial Tobacco|T&T|KT&G|VTN Tobacco)\s+', '', product_name, flags=re.IGNORECASE)
                    product_name = product_name.strip()
                    
                    if not product_name or len(product_name) < 3:
                        continue
                    
                    # Normalize et
                    product_name_normalized = _normalize_product_name(product_name)
                    if product_name_normalized:
                        product_name = product_name_normalized
                    
                    # Slash ile ayrılmış ürünleri ayrı ayrı ekle
                    if "/" in product_name:
                        variants = [v.strip() for v in product_name.split("/")]
                        for variant in variants:
                            if variant and len(variant) > 3:
                                variant_key = (variant.lower(), price)
                                if variant_key not in seen_products:
                                    seen_products.add(variant_key)
                                    results.append({
                                        "product_name": variant,
                                        "price": round(price, 2),
                                        "currency": "TRY",
                                        "market_name": self.MARKET_NAME,
                                        "product_id": None,
                                        "image_url": None,
                                        "is_tobacco": False,  # Metin içindeki fiyatlar sigara paketi
                                    })
                    else:
                        product_key = (product_name.lower(), price)
                        if product_key not in seen_products:
                            seen_products.add(product_key)
                            results.append({
                                "product_name": product_name,
                                "price": round(price, 2),
                                "currency": "TRY",
                                "market_name": self.MARKET_NAME,
                                "product_id": None,
                                "image_url": None,
                                "is_tobacco": False,
                            })
        except Exception as exc:
            logger.warning("[Tekel] Metin fiyat parse hatası: %s", exc)
        
        return results

    def _parse_table_rows(self, soup: BeautifulSoup) -> list[dict[str, Any]]:
        """
        BeautifulSoup objesindeki tüm tabloları parse eder.
        
        Returns:
            Ürün listesi: [{"product_name": str, "price": float, ...}, ...]
        """
        results: list[dict[str, Any]] = []
        
        try:
            # Tüm <table> etiketlerini bul
            tables = soup.find_all("table")
            
            if not tables:
                # Alternatif: div içinde tablo benzeri yapılar olabilir
                # veya farklı bir HTML yapısı olabilir
                html_text = str(soup)
                if "table" in html_text.lower():
                    logger.warning("[Tekel] HTML'de 'table' kelimesi geçiyor ama BeautifulSoup bulamadı. Parser sorunu olabilir.")
                    # lxml parser'ı dene
                    try:
                        soup_lxml = BeautifulSoup(html_text, "lxml")
                        tables = soup_lxml.find_all("table")
                        if tables:
                            logger.info("[Tekel] lxml parser ile %d tablo bulundu", len(tables))
                            soup = soup_lxml
                        else:
                            logger.warning("[Tekel] Tablo bulunamadı (HTML uzunluğu: %d)", len(soup.get_text()))
                            return results
                    except Exception:
                        logger.warning("[Tekel] Tablo bulunamadı (HTML uzunluğu: %d)", len(soup.get_text()))
                        return results
                else:
                    logger.warning("[Tekel] Tablo bulunamadı (HTML uzunluğu: %d)", len(soup.get_text()))
                    return results
            
            logger.debug("[Tekel] %d tablo bulundu", len(tables))
            
            for table in tables:
                # Her tablodaki <tr> satırlarını döngüye al
                rows = table.find_all("tr")
                
                for row in rows:
                    try:
                        # <td> ve <th> hücrelerini bul
                        cells = row.find_all(["td", "th"])
                        
                        # En az 2 sütun olmalı
                        if len(cells) < 2:
                            continue
                        
                        # İlk sütun: Ürün Adı
                        product_name_raw = cells[0].get_text(strip=True)
                        if not product_name_raw:
                            continue
                        
                        # Başlık satırını atla (Ürün, Miktar, Fiyat gibi)
                        if product_name_raw.lower() in ["ürün", "product", "miktar", "fiyat", "price"]:
                            continue
                        
                        # Miktar kontrolü: Eğer miktar sütununda "GR" geçiyorsa bu bir tütün ürünü
                        # Sigara paketleri için miktar genellikle yok veya "paket" gibi bir şey
                        is_tobacco = False
                        if len(cells) >= 3:
                            miktar_text = cells[1].get_text(strip=True).upper()
                            if "GR" in miktar_text or "GRAM" in miktar_text or "KG" in miktar_text or "KILO" in miktar_text:
                                is_tobacco = True
                                # Tütün ürünlerini atla (kullanıcı sigara paketi fiyatını bekliyor)
                                continue
                        
                        # Fiyat sütunu: 3 sütun varsa 3. sütun, yoksa 2. sütun
                        if len(cells) >= 3:
                            # Format: Ürün | Miktar | Fiyat
                            price_text = cells[2].get_text(strip=True)
                        else:
                            # Format: Ürün | Fiyat
                            price_text = cells[1].get_text(strip=True)
                        
                        if not price_text:
                            continue
                        
                        # Fiyatı parse et
                        price = _parse_price(price_text)
                        if price is None or price <= 0:
                            continue
                        
                        # Ürün adını normalize et
                        product_name = _normalize_product_name(product_name_raw)
                        if not product_name:
                            # Normalize edilmiş ad boşsa, orijinal adı kullan
                            product_name = product_name_raw.strip()
                        
                        # Tütün ürünlerini işaretle (kullanıcı sigara paketi fiyatını bekliyor olabilir)
                        # Ancak şimdilik tüm ürünleri ekleyelim, kategori bilgisi ile ayırt edilebilir
                        results.append({
                            "product_name": product_name,
                            "price": round(price, 2),
                            "currency": "TRY",
                            "market_name": self.MARKET_NAME,
                            "product_id": None,  # karekod.org'da ID yok
                            "image_url": None,  # Tablolarda görsel yok
                            "is_tobacco": is_tobacco,  # Tütün mü sigara paketi mi?
                        })
                        
                    except Exception as exc:
                        logger.debug("[Tekel] Satır parse hatası: %s", exc)
                        continue
                        
        except Exception as exc:
            logger.warning("[Tekel] Tablo parse hatası: %s", exc)
        
        return results

    async def get_alcohol_prices(self) -> list[dict[str, Any]]:
        """
        karekod.org alkol fiyatları sayfasından fiyatları çeker.
        
        Returns:
            Alkol ürünleri listesi
        """
        # Önce cache'e bak
        cache_key = "alcohol:prices"
        cached = await self._get_cached(cache_key)
        if cached:
            logger.info("[Tekel] Cache hit: alkol fiyatları")
            return cached
        
        results: list[dict[str, Any]] = []
        
        try:
            # HTTP isteği (Accept-Encoding header'ını kaldır, gzip sorunları olabilir)
            # _make_request metoduna custom headers geç
            custom_headers = {
                "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            }
            response = await self._make_request(KAREKOD_ALCOHOL_URL, headers=custom_headers)
            
            # Response encoding kontrolü ve decode
            # httpx otomatik decompress eder ama encoding sorunları olabilir
            try:
                html_text = response.text
            except UnicodeDecodeError:
                # Encoding sorunu varsa manuel decode et
                html_text = response.content.decode('utf-8', errors='ignore')
            
            logger.info("[Tekel] Alkol sayfası yüklendi: %d karakter, status: %d, encoding: %s", 
                       len(html_text), response.status_code, response.encoding)
            
            # BeautifulSoup ile parse (lxml parser daha güvenilir)
            try:
                soup = BeautifulSoup(html_text, "lxml")
            except Exception:
                # lxml yoksa html.parser kullan
                soup = BeautifulSoup(html_text, "html.parser")
            
            # Debug: HTML içinde "table" kelimesi var mı kontrol et
            html_lower = response.text.lower()
            if "<table" in html_lower:
                logger.info("[Tekel] HTML'de <table> etiketi bulundu")
            else:
                logger.warning("[Tekel] HTML'de <table> etiketi bulunamadı")
            
            # Tabloları parse et
            table_results = self._parse_table_rows(soup)
            logger.info("[Tekel] Alkol tablo parse sonucu: %d ürün", len(table_results))
            
            # Metin içindeki fiyatları da parse et
            text_results = self._parse_text_prices(soup)
            logger.info("[Tekel] Alkol metin parse sonucu: %d ürün", len(text_results))
            
            # Birleştir ve deduplicate et
            results = table_results + text_results
            # Aynı ürün adı ve fiyatı olanları tekilleştir
            seen = set()
            unique_results = []
            for item in results:
                key = (item["product_name"].lower(), item["price"])
                if key not in seen:
                    seen.add(key)
                    unique_results.append(item)
            results = unique_results
            
            # Kategori bilgisi ekle
            for item in results:
                item["category"] = "alcohol"
            
            # Sonuçları cache'le (12 saat TTL)
            if results:
                await self._set_cache(cache_key, results, ttl=TEKEL_CACHE_TTL)
                logger.info("[Tekel] Alkol fiyatları çekildi: %d ürün", len(results))
            else:
                logger.warning("[Tekel] Alkol fiyatları bulunamadı")
                
        except Exception as exc:
            logger.error("[Tekel] Alkol fiyatları çekme hatası: %s", exc)
        
        return results

    async def get_cigarette_prices(self) -> list[dict[str, Any]]:
        """
        karekod.org sigara fiyatları sayfasından fiyatları çeker.
        
        Returns:
            Sigara ürünleri listesi
        """
        # Önce cache'e bak
        cache_key = "cigarette:prices"
        cached = await self._get_cached(cache_key)
        if cached:
            logger.info("[Tekel] Cache hit: sigara fiyatları")
            return cached
        
        results: list[dict[str, Any]] = []
        
        try:
            # HTTP isteği (Accept-Encoding header'ını kaldır, gzip sorunları olabilir)
            # _make_request metoduna custom headers geç
            custom_headers = {
                "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            }
            response = await self._make_request(KAREKOD_CIGARETTE_URL, headers=custom_headers)
            
            # Response encoding kontrolü ve decode
            # httpx otomatik decompress eder ama encoding sorunları olabilir
            try:
                html_text = response.text
            except UnicodeDecodeError:
                # Encoding sorunu varsa manuel decode et
                html_text = response.content.decode('utf-8', errors='ignore')
            
            logger.info("[Tekel] Sigara sayfası yüklendi: %d karakter, status: %d, encoding: %s", 
                       len(html_text), response.status_code, response.encoding)
            
            # BeautifulSoup ile parse (lxml parser daha güvenilir)
            try:
                soup = BeautifulSoup(html_text, "lxml")
            except Exception:
                # lxml yoksa html.parser kullan
                soup = BeautifulSoup(html_text, "html.parser")
            
            # Debug: HTML içinde "table" kelimesi var mı kontrol et
            html_lower = html_text.lower()
            if "<table" in html_lower:
                logger.info("[Tekel] HTML'de <table> etiketi bulundu")
            else:
                logger.warning("[Tekel] HTML'de <table> etiketi bulunamadı")
            
            # Tabloları parse et
            table_results = self._parse_table_rows(soup)
            logger.info("[Tekel] Sigara tablo parse sonucu: %d ürün", len(table_results))
            
            # Metin içindeki fiyatları da parse et
            text_results = self._parse_text_prices(soup)
            logger.info("[Tekel] Sigara metin parse sonucu: %d ürün", len(text_results))
            
            # Birleştir ve deduplicate et
            results = table_results + text_results
            # Aynı ürün adı ve fiyatı olanları tekilleştir
            seen = set()
            unique_results = []
            for item in results:
                key = (item["product_name"].lower(), item["price"])
                if key not in seen:
                    seen.add(key)
                    unique_results.append(item)
            results = unique_results
            
            # Kategori bilgisi ekle
            for item in results:
                item["category"] = "cigarette"
            
            # Sonuçları cache'le (12 saat TTL)
            if results:
                await self._set_cache(cache_key, results, ttl=TEKEL_CACHE_TTL)
                logger.info("[Tekel] Sigara fiyatları çekildi: %d ürün", len(results))
            else:
                logger.warning("[Tekel] Sigara fiyatları bulunamadı")
                
        except Exception as exc:
            logger.error("[Tekel] Sigara fiyatları çekme hatası: %s", exc)
        
        return results

    async def search_product(self, query: str) -> list[dict[str, Any]]:
        """
        Alkol ve sigara listelerinde query'yi case-insensitive arar.
        Türkçe-İngilizce renk eşleştirmesi yapar (gri -> grey, mavi -> blue).
        
        Args:
            query: Aranacak ürün adı
            
        Returns:
            Eşleşen ürünler listesi
        """
        if not query or not query.strip():
            return []
        
        query_lower = query.lower().strip()
        
        # Türkçe-İngilizce renk eşleştirmesi
        color_map = {
            "gri": "grey",
            "mavi": "blue",
            "beyaz": "white",
            "siyah": "black",
            "kırmızı": "red",
            "sarı": "yellow",
            "yeşil": "green",
            "turuncu": "orange",
            "mor": "purple",
            "pembe": "pink",
        }
        
        # Query'yi genişlet: Türkçe renkleri İngilizce karşılıklarıyla değiştir
        expanded_queries = [query_lower]
        for tr_color, en_color in color_map.items():
            if tr_color in query_lower:
                expanded_queries.append(query_lower.replace(tr_color, en_color))
                # Hem Türkçe hem İngilizce versiyonu ekle
                expanded_queries.append(query_lower.replace(tr_color, f"{tr_color} {en_color}"))
        
        # Önce cache'e bak
        cache_key = f"search:{query_lower}"
        cached = await self._get_cached(cache_key)
        if cached:
            logger.info("[Tekel] Cache hit: %s", query)
            return cached
        
        results: list[dict[str, Any]] = []
        
        try:
            # Alkol ve sigara fiyatlarını al (cache'ten okur)
            alcohol_products = await self.get_alcohol_prices()
            cigarette_products = await self.get_cigarette_prices()
            
            # Her iki listede query'yi case-insensitive ara
            all_products = alcohol_products + cigarette_products
            
            seen_keys = set()  # Deduplication için
            
            for product in all_products:
                product_name_lower = product.get("product_name", "").lower()
                
                # Genişletilmiş query'lerden herhangi biri ürün adında geçiyorsa ekle
                for expanded_query in expanded_queries:
                    # Query kelimelerini ayrı ayrı kontrol et (daha esnek eşleşme)
                    query_words = expanded_query.split()
                    if len(query_words) > 1:
                        # Tüm kelimeler ürün adında geçiyorsa eşleşme kabul et
                        if all(word in product_name_lower for word in query_words):
                            product_key = (product.get("product_name"), product.get("price"))
                            if product_key not in seen_keys:
                                seen_keys.add(product_key)
                                results.append(product)
                            break
                    else:
                        # Tek kelime ise direkt kontrol et
                        if expanded_query in product_name_lower:
                            product_key = (product.get("product_name"), product.get("price"))
                            if product_key not in seen_keys:
                                seen_keys.add(product_key)
                                results.append(product)
                            break
            
            # Sonuçları cache'le (12 saat TTL)
            if results:
                await self._set_cache(cache_key, results, ttl=TEKEL_CACHE_TTL)
                logger.info("[Tekel] '%s' araması: %d ürün bulundu", query, len(results))
            else:
                logger.info("[Tekel] '%s' araması: sonuç bulunamadı", query)
                
        except Exception as exc:
            logger.error("[Tekel] Arama hatası '%s': %s", query, exc)
        
        return results

    async def get_product_price(self, product_id: str) -> dict[str, Any] | None:
        """
        Belirli bir ürünün fiyatını getirir (search ile).
        
        Args:
            product_id: Ürün adı (karekod.org'da ID yok, isim kullanılır)
            
        Returns:
            Ürün bilgisi veya None
        """
        results = await self.search_product(product_id)
        return results[0] if results else None
