"""
SokScraper – ŞOK Market: RSC format desteği ile API benzeri ürün arama.

ŞOK'un Next.js RSC endpoint'ini (https://www.sokmarket.com.tr/arama?q=...&_rsc=...)
kullanarak JSON benzeri yapıları parse eder. iPhone User-Agent kullanarak bot algılamayı önler.

Özellikler:
- RSC format parse (initialSearchResult -> results)
- JSON benzeri yapıları HTML'den çıkarma
- Kuruş dönüşümü: MigrosScraper benzeri mantık
"""

from __future__ import annotations

import codecs
import json
import logging
import re
import unicodedata
from typing import Any

from scrapers.base_scraper import AbstractBaseScraper

logger = logging.getLogger(__name__)

# API endpoint
SOK_BASE_URL = "https://www.sokmarket.com.tr"
SOK_SEARCH_URL = f"{SOK_BASE_URL}/arama"

# iPhone User-Agent (bot algılamayı önlemek için)
SOK_IPHONE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.6 Mobile/15E148 Safari/604.1"
)

# API header'ları
SOK_API_HEADERS = {
    "User-Agent": SOK_IPHONE_USER_AGENT,
    "Referer": SOK_BASE_URL,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Fiyat dönüşümü için threshold (MigrosScraper benzeri)
SOK_PRICE_DIVISOR = 100.0
_SOK_KURUS_THRESHOLD = 1000.0

# Veri filtreleme: Alakasız ürünleri elemek için kara liste
# Encoding sorunları nedeniyle hem normal hem de encoding sorunlu versiyonlar kontrol edilir
EXCLUDED_KEYWORDS = [
    'noodle', 'mama', 'çorba', 'bulyon', 'çeşni', 'harç', 'pane', 'sos',
    # Encoding sorunlu versiyonlar (Türkçe karakterler bozuk görünebilir)
    'corba', 'cesni', 'harc', 'pedi', 'ped',
    # Encoding sorunlu karakter kombinasyonları (örn: "Ã§orbasÄ±" -> "orbas" kalır)
    'orbas',    # çorba -> encoding sorunu -> "orbas"
    'cesn',     # çeşni -> encoding sorunu -> "cesn"
    'cesnili',  # çeşnili -> encoding sorunu -> "cesnili"
    'cesnisi',  # çeşnisi -> encoding sorunu -> "cesnisi"
    'eånili',   # çeşnili -> encoding sorunu -> "eånili" (ãeånili)
    'eånisi',   # çeşnisi -> encoding sorunu -> "eånisi" (ãeånisi)
    'eå',       # çeş -> encoding sorunu -> "eå" (ãeå)
    'harc',     # harç -> encoding sorunu -> "harc"
    'eriste',   # erişte -> encoding sorunu -> "eriste"
    'eriåte',   # erişte -> encoding sorunu -> "eriåte"
    'eriå',     # erişte -> encoding sorunu -> "eriå" (eriåte)
]

# Kategori bazlı filtreleme: Bu kategorilerdeki ürünler filtrelenir
EXCLUDED_CATEGORIES = ['evcil-dostlar', 'hazir-yemek-ve-meze']


def _safe_price(raw: float | int) -> float:
    """
    ŞOK fiyatını TL'ye çevirir.
    Fiyat > 1000 ise kuruş kabul edilip 100'e bölünür; aksi hâlde TL'dir.
    """
    raw_float = float(raw)
    if raw_float > _SOK_KURUS_THRESHOLD:
        return round(raw_float / SOK_PRICE_DIVISOR, 2)
    return round(raw_float, 2)


class SokScraper(AbstractBaseScraper):
    """ŞOK – RSC format desteği ile hızlı ürün arama."""

    MARKET_NAME = "ŞOK"

    async def search_product(self, query: str) -> list[dict[str, Any]]:
        """
        ŞOK RSC endpoint'inden ürün arar.
        
        Öncelik sırası:
        1. Cache kontrolü
        2. RSC API isteği (_search_rsc)
        3. RSC response parse
        4. Sonuçları cache'le
        """
        cache_key = f"search:{query.lower().strip()}"
        cached = await self._get_cached(cache_key)
        if cached:
            logger.info("[ŞOK] Cache hit: %s", query)
            return cached

        results = await self._search_rsc(query)

        if results:
            await self._set_cache(cache_key, results)
        return results

    async def _search_rsc(self, query: str) -> list[dict[str, Any]]:
        """
        ŞOK RSC endpoint'inden ürün arar.
        
        Parametreler:
        - q: {query}
        - _rsc: RSC token (opsiyonel, önce token olmadan dene)
        """
        results: list[dict[str, Any]] = []

        try:
            # RSC token olmadan önce dene
            search_url = f"{SOK_SEARCH_URL}?q={query}"
            
            # Header'ları merge et (DEFAULT_HEADERS ile birleştir)
            merged_headers = dict(self.DEFAULT_HEADERS)
            merged_headers.update(SOK_API_HEADERS)
            
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
                    search_url,
                    headers=merged_headers,
                )
                response.raise_for_status()

                # RSC response parse
                text = response.text
                logger.info("[ŞOK] Response text length: %d", len(text) if text else 0)
                data = self._parse_rsc_response(text)
                logger.info("[ŞOK] Parsed data: %s", "None" if data is None else f"{type(data).__name__} with keys: {list(data.keys())[:5] if isinstance(data, dict) else 'N/A'}")
            
            # ŞOK RSC response formatı: initialSearchResult -> results
            products = []
            if isinstance(data, dict):
                # Format 1: {"initialSearchResult": {"results": [...]}}
                if "initialSearchResult" in data:
                    initial_result = data["initialSearchResult"]
                    if isinstance(initial_result, dict) and "results" in initial_result:
                        products = initial_result["results"]
                # Format 2: {"props": {"pageProps": {"initialSearchResult": {"results": [...]}}}}
                elif "props" in data:
                    props = data["props"]
                    if isinstance(props, dict) and "pageProps" in props:
                        page_props = props["pageProps"]
                        if isinstance(page_props, dict) and "initialSearchResult" in page_props:
                            initial_result = page_props["initialSearchResult"]
                            if isinstance(initial_result, dict) and "results" in initial_result:
                                products = initial_result["results"]
                # Format 3: Direkt results dizisi
                elif "results" in data:
                    products = data["results"]
            elif isinstance(data, list):
                # Format 4: Direkt liste
                products = data

            logger.info("[ŞOK] Products extracted: %d items", len(products) if products else 0)

            # Ürünleri parse et
            parsed_count = 0
            skipped_count = 0
            for item in products[:60]:  # İlk 60 ürün
                try:
                    # Ürün adı: product -> name
                    product_data = item.get("product", {})
                    if not isinstance(product_data, dict):
                        product_data = {}
                    
                    product_name = product_data.get("name") or item.get("name") or item.get("title")
                    if not product_name or len(product_name) < 3:
                        skipped_count += 1
                        continue

                    # Encoding düzeltmesi: ŞOK'tan gelen ürün adlarında encoding sorunları var
                    # Örn: "SÃ¼t" -> "Süt", "MÄ±sÄ±r" -> "Mısır"
                    product_name = self._fix_encoding(product_name)

                    # Veri filtreleme: Kara liste kontrolü
                    # Encoding sorunlarını handle etmek için normalize edilmiş versiyonu kontrol et
                    product_name_lower = product_name.lower()
                    
                    # Unicode normalize: Encoding sorunlu karakterleri düzelt
                    # Önce NFKD normalize et (decompose), sonra ASCII'ye yakın karakterlere çevir
                    try:
                        # Unicode normalize ile encoding sorunlarını düzelt
                        normalized_unicode = unicodedata.normalize('NFKD', product_name_lower)
                        # ASCII olmayan karakterleri kaldır veya ASCII'ye yakın karakterlere çevir
                        normalized_name = ''.join(
                            c for c in normalized_unicode 
                            if not unicodedata.combining(c)
                        )
                        # Kalan encoding sorunlu karakterleri temizle
                        normalized_name = normalized_name.encode('ascii', 'ignore').decode('ascii')
                    except Exception:
                        # Fallback: Basit normalize
                        normalized_name = product_name_lower
                        # Yaygın encoding sorunlu karakterleri düzelt
                        encoding_fixes = {
                            'ã': 'a', 'ä': 'a', 'å': 'a',
                            'ç': 'c',
                            'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
                            'í': 'i', 'ì': 'i', 'î': 'i', 'ï': 'i',
                            'ó': 'o', 'ò': 'o', 'ô': 'o', 'ö': 'o',
                            'ú': 'u', 'ù': 'u', 'û': 'u', 'ü': 'u',
                            'ş': 's',
                            'ğ': 'g',
                        }
                        for old_char, new_char in encoding_fixes.items():
                            normalized_name = normalized_name.replace(old_char, new_char)
                    
                    # Kara liste kontrolü: Hem orijinal hem normalize edilmiş isimde kontrol et
                    if any(
                        keyword in product_name_lower or keyword in normalized_name
                        for keyword in EXCLUDED_KEYWORDS
                    ):
                        logger.debug("[ŞOK] Ürün kara listede: %s", product_name)
                        continue

                    # Kategori bazlı filtreleme: breadCrumbs kontrolü
                    sku_data = item.get("sku", {})
                    if isinstance(sku_data, dict):
                        breadcrumbs = sku_data.get("breadCrumbs", [])
                        if isinstance(breadcrumbs, list):
                            # breadcrumbs içindeki code değerlerini kontrol et
                            if any(
                                isinstance(bc, dict) and bc.get("code") in EXCLUDED_CATEGORIES
                                for bc in breadcrumbs
                            ):
                                logger.debug("[ŞOK] Ürün hariç kategoride: %s", product_name)
                                continue

                    # Fiyat: prices -> discounted -> value
                    prices_data = item.get("prices", {})
                    if not isinstance(prices_data, dict):
                        prices_data = {}
                    
                    discounted_data = prices_data.get("discounted", {})
                    if not isinstance(discounted_data, dict):
                        discounted_data = {}
                    
                    raw_price = discounted_data.get("value") or prices_data.get("value") or item.get("price")
                    if raw_price is None:
                        continue
                    
                    try:
                        price = float(raw_price)
                    except (ValueError, TypeError):
                        continue
                    
                    if price <= 0:
                        continue
                    
                    # Kuruş dönüşümü (gerekirse)
                    price = _safe_price(price)

                    # Görsel: product -> images[0] -> host + path
                    images = product_data.get("images", [])
                    image_url = None
                    if images and len(images) > 0:
                        first_img = images[0]
                        if isinstance(first_img, dict):
                            host = first_img.get("host", "")
                            path = first_img.get("path", "")
                            if host and path:
                                # host zaten tam URL olabilir veya sadece domain olabilir
                                if host.startswith("http"):
                                    image_url = f"{host}{path}" if path.startswith("/") else f"{host}/{path}"
                                else:
                                    image_url = f"https://{host}{path}" if path.startswith("/") else f"https://{host}/{path}"
                    
                    # Alternatif görsel kaynakları
                    if not image_url:
                        image_url = item.get("image") or item.get("imageUrl") or item.get("image_url")

                    # ID: id alanı
                    product_id = str(item.get("id", ""))

                    # Gramaj (ürün adından çıkar)
                    gramaj = self._parse_gramaj_from_name(product_name)

                    # URL (varsa)
                    product_url = item.get("url") or item.get("link")

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
                    parsed_count += 1

                except Exception as exc:
                    skipped_count += 1
                    logger.debug("[ŞOK] Ürün parse hatası: %s", exc)
                    continue
            
            logger.info("[ŞOK] Parse summary: %d parsed, %d skipped, %d results", parsed_count, skipped_count, len(results))

        except Exception as exc:
            logger.warning("[ŞOK] RSC API hatası '%s': %s", query, exc)

        # Sonuç sınırı: En alakalı ilk 30 temiz sonucu dön
        filtered_results = results[:30]
        if len(results) > 30:
            logger.info("[ŞOK] %d sonuç bulundu, ilk 30'u döndürülüyor", len(results))

        return filtered_results

    def _parse_rsc_response(self, text: str) -> dict[str, Any] | list[Any] | None:
        """
        RSC response'undan JSON benzeri yapıları çıkarır.
        
        Formatlar:
        1. HTML içinde gömülü JSON: <script id="__NEXT_DATA__">...</script>
        2. Direkt JSON response
        3. Escaped JSON içinde initialSearchResult (JavaScript string literal)
        4. RSC payload içinde JSON benzeri yapılar
        """
        # Format 1: HTML içinde gömülü JSON (<script id="__NEXT_DATA__">)
        next_data_match = re.search(
            r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*type=["\']application/json["\'][^>]*>(.*?)</script>',
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if next_data_match:
            try:
                json_str = next_data_match.group(1).strip()
                return json.loads(json_str)
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug("[ŞOK] __NEXT_DATA__ parse hatası: %s", e)

        # Format 2: Direkt JSON response (text JSON ile başlıyorsa)
        text_stripped = text.strip()
        if text_stripped.startswith("{") or text_stripped.startswith("["):
            try:
                return json.loads(text_stripped)
            except (json.JSONDecodeError, ValueError):
                pass

        # Format 3: Escaped JSON içinde initialSearchResult bul
        # ŞOK'un response'unda JSON escaped olarak görünüyor (\u0026 gibi)
        if "initialSearchResult" in text:
            data = self._extract_escaped_json(text)
            if data:
                return data

        # Format 4: Regex ile initialSearchResult içeren JSON bloğunu bul
        pattern = r'"initialSearchResult"\s*:\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})'
        matches = re.finditer(pattern, text, re.DOTALL)
        for match in matches:
            data = self._extract_json_block(text, match.start())
            if data:
                return data

        logger.warning("[ŞOK] RSC response'undan JSON çıkarılamadı")
        return None

    def _extract_escaped_json(self, text: str) -> dict[str, Any] | None:
        """Escaped JSON içinden initialSearchResult bloğunu çıkarır."""
        idx = text.find("initialSearchResult")
        if idx <= 0:
            return None

        # Geriye doğru { bul
        start_idx = text.rfind("{", 0, idx)
        if start_idx <= 0:
            return None

        # Balanced braces ile JSON bloğunu çıkar
        json_str = self._extract_balanced_json(text, start_idx)
        if not json_str:
            return None

        # Unicode escape sequence'ları decode et
        try:
            decoded = codecs.decode(json_str, 'unicode_escape')
            if "\\u" in decoded:
                decoded = codecs.decode(decoded, 'unicode_escape')
            data = json.loads(decoded)
            if isinstance(data, dict) and "initialSearchResult" in data:
                return data
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
            logger.debug("[ŞOK] Escaped JSON decode hatası: %s", e)
            # Alternatif: Direkt parse dene
            try:
                data = json.loads(json_str)
                if isinstance(data, dict) and "initialSearchResult" in data:
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

        return None

    def _extract_json_block(self, text: str, match_start: int) -> dict[str, Any] | None:
        """Regex match'inden JSON bloğunu çıkarır."""
        json_start = text.rfind("{", 0, match_start)
        if json_start <= 0:
            return None

        json_str = self._extract_balanced_json(text, json_start, max_length=500000)
        if not json_str:
            return None

        # Unicode decode dene
        try:
            decoded = codecs.decode(json_str, 'unicode_escape')
            if "\\u" in decoded:
                decoded = codecs.decode(decoded, 'unicode_escape')
            data = json.loads(decoded)
            if isinstance(data, dict) and "initialSearchResult" in data:
                return data
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            # Direkt parse dene
            try:
                data = json.loads(json_str)
                if isinstance(data, dict) and "initialSearchResult" in data:
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

        return None

    @staticmethod
    def _extract_balanced_json(text: str, start_idx: int, max_length: int = 0) -> str | None:
        """Balanced braces ile JSON bloğunu çıkarır."""
        brace_count = 0
        end_idx = start_idx
        in_string = False
        escape_next = False
        max_pos = len(text) if max_length == 0 else min(start_idx + max_length, len(text))

        for i in range(start_idx, max_pos):
            char = text[i]
            if escape_next:
                escape_next = False
                continue
            if char == "\\":
                escape_next = True
                continue
            if char == '"' and not escape_next:
                in_string = not in_string
                continue
            if not in_string:
                if char == "{":
                    brace_count += 1
                elif char == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i + 1
                        break

        if end_idx > start_idx:
            return text[start_idx:end_idx]
        return None

    @staticmethod
    def _fix_encoding(text: str) -> str:
        """
        ŞOK'tan gelen encoding sorunlu metinleri düzeltir.
        ŞOK'un API'sinden gelen veriler UTF-8 olarak yanlış decode edilmiş olabilir.
        Örn: "SÃ¼t" -> "Süt", "MÄ±sÄ±r" -> "Mısır", "YaÄ\x9flÄ±" -> "Yağlı"
        """
        if not text:
            return text
        
        try:
            fixed_text = text
            
            # Adım 1: Önce özel karakter kombinasyonlarını düzelt (uzun kombinasyonlar önce)
            special_combinations = {
                'YaÄ\x9fs': 'Yağs',      # YaÄ\x9fsÄ±z -> Yağsız
                'YaÄ\x9fl': 'Yağl',      # YaÄ\x9flÄ± -> Yağlı
                'Ä\x9fs': 'ğs',         # Genel
                'Ä\x9fl': 'ğl',         # Genel
                '\x9fsÄ': 'ğsı',        # Genel
                '\x9flÄ': 'ğlı',        # Genel
                'ıÇ': 'Ç',              # ıÇilekli -> Çilekli
                'ıç': 'ç',              # ıçilekli -> çilekli
                'ıĞ': 'Ğ',              # Genel
                'ığ': 'ğ',              # Genel
                'ıŞ': 'Ş',              # Genel
                'ış': 'ş',              # Genel
                'ıÜ': 'Ü',              # Genel
                'ıü': 'ü',              # Genel
                'ıÖ': 'Ö',              # Genel
                'ıö': 'ö',              # Pastıörize -> Pastörize
                'ıİ': 'İ',              # Genel
                'ı¶': 'ö',              # Pastı¶rize -> Pastörize
                'Åğ': 'ş',              # SütaÅğ -> Sütaş
                'şğ': 'ş',              # Sütaşğ -> Sütaş
                'ıÇi': 'Çi',            # ıÇilekli -> Çilekli (ekstra)
                'ıçi': 'çi',            # ıçilekli -> çilekli (ekstra)
            }
            
            for wrong, correct in sorted(special_combinations.items(), key=lambda x: -len(x[0])):
                fixed_text = fixed_text.replace(wrong, correct)
            
            # Adım 2: Tek karakter encoding sorunlarını düzelt
            single_char_fixes = {
                'Ã¼': 'ü',      # SÃ¼t -> Süt
                'Ã§': 'ç',      # Ã§ilek -> çilek
                'Ä±': 'ı',      # MÄ±sÄ±r -> Mısır
                'Ä°': 'İ',      # Ä°Ã§im -> İçim
                '¼': 'ü',       # SÃ¼t -> Süt (alternatif)
                '±': 'ı',       # MÄ±sÄ±r -> Mısır (alternatif)
                '\x9f': 'ğ',    # YaÄ\x9f -> Yağ
                'Å': 'ş',       # SütaÅ -> Sütaş
                '¶': 'ö',       # Pastörize -> Pastörize
            }
            
            for wrong, correct in single_char_fixes.items():
                fixed_text = fixed_text.replace(wrong, correct)
            
            # Adım 3: Kalan genel fallback'ler
            general_fixes = {
                'Ã': 'ı',       # Genel fallback
                'Ä': 'ı',       # Genel fallback
            }
            
            for wrong, correct in general_fixes.items():
                fixed_text = fixed_text.replace(wrong, correct)
            
            # Adım 4: "ı" karakterinden sonra gelen Türkçe karakterleri düzelt (regex ile)
            # Örn: "ıÇ" -> "Ç", "ıö" -> "ö", "ış" -> "ş"
            # Regex kullanarak daha güvenilir düzeltme
            import re
            # "ı" karakterinden sonra gelen Türkçe karakterleri kaldır
            # Hem büyük hem küçük harfleri kapsar
            turkish_chars = 'ÇĞİÖŞÜçğıöşü'
            pattern = f'ı([{turkish_chars}])'
            fixed_text = re.sub(pattern, r'\1', fixed_text)
            
            # Ayrıca manuel düzeltmeler (fallback - tekrar kontrol)
            turkish_after_i = {
                'ıÇ': 'Ç', 'ıç': 'ç',
                'ıĞ': 'Ğ', 'ığ': 'ğ',
                'ıŞ': 'Ş', 'ış': 'ş',
                'ıÜ': 'Ü', 'ıü': 'ü',
                'ıÖ': 'Ö', 'ıö': 'ö',
                'ıİ': 'İ', 'ıı': 'ı',
            }
            
            # Tekrar düzelt (bazı karakterler regex'ten kaçmış olabilir)
            for wrong, correct in turkish_after_i.items():
                fixed_text = fixed_text.replace(wrong, correct)
            
            # Son bir kontrol: Eğer hala "ı" karakterinden sonra Türkçe karakter varsa
            # Regex ile tekrar düzelt
            fixed_text = re.sub(pattern, r'\1', fixed_text)
            
            # Adım 5: "şğ" -> "ş" gibi çift karakter sorunlarını düzelt
            double_char_fixes = {
                'şğ': 'ş',
                'Şğ': 'Ş',
            }
            
            for wrong, correct in double_char_fixes.items():
                fixed_text = fixed_text.replace(wrong, correct)
            
            return fixed_text
            
        except Exception as e:
            logger.debug("[ŞOK] Encoding düzeltme hatası: %s", e)
            return text

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
        """ŞOK scraper kapatılırken parent close."""
        await super().close()
