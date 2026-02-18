"""
FilterService – Veri kalitesi pipeline'ı.

Sırasıyla:
  1. Kara liste (blacklist) filtresi
  2. Dinamik anahtar kelime kontrolü
  3. Deduplication (tekilleştirme)
  4. 1 kg normalize fiyat hesaplama
  5. AI Re-Ranking (Gemini, opsiyonel – kota doluysa fallback)
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from services.ai_service import AIService

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  Sabitler
# ═══════════════════════════════════════════════════════════════════════════════

# 1a – Kara liste: ürün adında bunlar varsa ve sorgu bunları içermiyorsa → ele
BLACKLIST_KEYWORDS: set[str] = {
    "ped", "noodle", "çorba", "bulyon", "sos", "deterjan",
    "şampuan", "sabun", "peçete", "tuvalet", "mendil", "parfüm",
    "diş macunu", "krem", "losyon", "deodorant", "çamaşır",
    "bulaşık", "fumesi", "baharat", "kedi", "köpek", "mama",
}

# 1b – Dinamik kontrol: sorgu → ürün adında geçmesi ZORUNLU kelimeler
#       Sorgudaki herhangi bir anahtar kelime eşleşirse o kuralı uygula
DYNAMIC_KEYWORD_MAP: dict[str, set[str]] = {
    "tavuk göğsü":  {"piliç", "tavuk", "bonfile", "göğüs", "gogus", "göğsü"},
    "tavuk":        {"piliç", "tavuk", "chicken"},
    "bonfile":      {"bonfile", "piliç", "tavuk", "göğüs"},
    "süt":          {"süt", "milk"},
    "yoğurt":       {"yoğurt", "yogurt"},
    "peynir":       {"peynir", "cheese"},
    "yumurta":      {"yumurta", "egg"},
    "pirinç":       {"pirinç", "baldo", "basmati"},
    "makarna":      {"makarna", "spagetti", "penne", "pasta"},
    "un":           {"un ", " un", "ekmeklik un", "çok amaçlı"},
    "şeker":        {"şeker", "toz şeker"},
    "zeytinyağı":   {"zeytinyağ", "sızma"},
    "ayçiçek yağı": {"ayçiçek", "ayçiçeği"},
    "kıyma":        {"kıyma", "dana", "kuzu"},
    "dana eti":     {"dana", "biftek", "antrikot", "kuşbaşı"},
}


class FilterService:
    """Ürün sonuçlarını temizleyen, normalize eden ve sıralayan servis."""

    def __init__(self, ai_service: "AIService") -> None:
        self._ai = ai_service

    # ── Ana Pipeline ─────────────────────────────────────────────────────────

    async def filter_and_rank(
        self,
        query: str,
        products: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Tam pipeline: blacklist → dinamik kelime → dedup → normalize → AI rerank.
        """
        count_in = len(products)

        # 1) Kara liste
        products = self._blacklist_filter(query, products)

        # 2) Dinamik anahtar kelime
        products = self._dynamic_keyword_filter(query, products)

        # 3) Deduplication
        products = self._deduplicate(products)

        # 4) 1 kg normalize fiyat
        products = self._normalize_unit_price(products)

        count_after_local = len(products)
        logger.info(
            "[FilterService] '%s': %d → %d ürün (yerel filtre). AI re-rank başlıyor…",
            query, count_in, count_after_local,
        )

        # 5) AI Re-Ranking (opsiyonel)
        products = await self._ai_rerank(query, products)

        # Son sıralama: unit_price varsa ona göre (en ekonomik), yoksa normalized_price_per_kg, yoksa price'a göre
        products.sort(
            key=lambda p: (
                p.get("unit_price") or p.get("unit_price_per_100") or float("inf"),
                p.get("normalized_price_per_kg") or float("inf"),
                p.get("price", float("inf")),
            )
        )

        logger.info("[FilterService] Sonuç: %d ürün.", len(products))
        return products

    # ── 1a. Kara Liste ───────────────────────────────────────────────────────

    @staticmethod
    def _blacklist_filter(
        query: str,
        products: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        query_lower = query.lower()
        result = []
        for p in products:
            name = p.get("product_name", "").lower()
            blocked = False
            for kw in BLACKLIST_KEYWORDS:
                if kw in name and kw not in query_lower:
                    blocked = True
                    break
            if not blocked:
                result.append(p)
        dropped = len(products) - len(result)
        if dropped:
            logger.debug("[FilterService] Kara liste: %d ürün elendi.", dropped)
        return result

    # ── 1b. Dinamik Anahtar Kelime ───────────────────────────────────────────

    @staticmethod
    def _dynamic_keyword_filter(
        query: str,
        products: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        query_lower = query.lower()

        # Hangi dinamik kural eşleşiyor?
        required_words: set[str] | None = None
        for trigger, words in DYNAMIC_KEYWORD_MAP.items():
            if trigger in query_lower:
                required_words = words
                break

        # Dinamik kural yoksa genel kelime eşleşmesini uygula
        if required_words is None:
            query_words = [w for w in query_lower.split() if len(w) >= 3]
            if not query_words:
                return products
            return [
                p for p in products
                if any(w in p.get("product_name", "").lower() for w in query_words)
            ]

        # Dinamik kural varsa zorunlu kelimelerden en az birini içermeli
        # Encoding sorunlarını handle etmek için normalize edilmiş versiyonu da kontrol et
        result = []
        
        # required_words'e normalize edilmiş versiyonları da ekle
        # Örn: "süt" -> {"süt", "milk", "sut"} (sut = normalize edilmiş süt)
        expanded_required_words = set(required_words)
        for rw in required_words:
            # Türkçe karakterleri ASCII'ye çevir
            normalized_rw = rw
            turkish_to_ascii = {
                'ü': 'u', 'Ü': 'u',
                'ö': 'o', 'Ö': 'o',
                'ı': 'i', 'İ': 'i',
                'ş': 's', 'Ş': 's',
                'ğ': 'g', 'Ğ': 'g',
                'ç': 'c', 'Ç': 'c',
            }
            for turkish, ascii_char in turkish_to_ascii.items():
                normalized_rw = normalized_rw.replace(turkish, ascii_char)
            if normalized_rw != rw:
                expanded_required_words.add(normalized_rw)
        
        for p in products:
            name = p.get("product_name", "").lower()
            
            # Normalize edilmiş isim (encoding sorunlarını düzelt)
            # ŞOK'tan gelen ürün adlarında encoding sorunları var (örn: "sã¼t" -> "süt")
            normalized_name = name
            try:
                # Önce encoding sorunlu karakter kombinasyonlarını düzelt
                # "ã¼" -> "ü" (UTF-8 encoding sorunu)
                encoding_combinations = {
                    'ã¼': 'ü',  # sã¼t -> süt
                    'ä±': 'ı',  # mä±sä±r -> mısır
                    'ã§': 'ç',  # çeşni
                    'ã': 'a',
                    'ä': 'a',
                    'å': 'a',
                    '±': 'ı',
                    '¼': 'ü',
                }
                for wrong, correct in encoding_combinations.items():
                    normalized_name = normalized_name.replace(wrong, correct)
                
                # Sonra Unicode normalize et
                normalized_unicode = unicodedata.normalize('NFKD', normalized_name)
                normalized_name = ''.join(
                    c for c in normalized_unicode 
                    if not unicodedata.combining(c)
                )
                
                # Türkçe karakterleri ASCII'ye yakın karakterlere çevir
                turkish_fixes = {
                    'ü': 'u', 'Ü': 'u',
                    'ö': 'o', 'Ö': 'o',
                    'ı': 'i', 'İ': 'i',
                    'ş': 's', 'Ş': 's',
                    'ğ': 'g', 'Ğ': 'g',
                    'ç': 'c', 'Ç': 'c',
                }
                for turkish, ascii_char in turkish_fixes.items():
                    normalized_name = normalized_name.replace(turkish, ascii_char)
            except Exception:
                normalized_name = name
            
            # Hem orijinal hem normalize edilmiş isimde kontrol et
            # expanded_required_words kullan (örn: "süt", "milk", "sut")
            matches = any(rw in name or rw in normalized_name for rw in expanded_required_words)
            if matches:
                result.append(p)
        
        dropped = len(products) - len(result)
        if dropped:
            logger.debug("[FilterService] Dinamik filtre: %d ürün elendi.", dropped)
        return result

    # ── 1c. Deduplication ────────────────────────────────────────────────────

    @staticmethod
    def _deduplicate(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        unique: list[dict[str, Any]] = []
        for p in products:
            key = (
                p.get("product_name", "").lower().strip(),
                p.get("market_name", "").lower(),
            )
            if key not in seen:
                seen.add(key)
                unique.append(p)
        dropped = len(products) - len(unique)
        if dropped:
            logger.debug("[FilterService] Dedup: %d mükerrer ürün silindi.", dropped)
        return unique

    # ── 1d. Birim Dönüştürücü (1 kg Normalize Fiyat) ────────────────────────

    @staticmethod
    def _normalize_unit_price(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Gramaj verisini kullanarak normalized_price_per_kg hesaplar.
        500g ürün → fiyat * 2 = 1 kg fiyat
        250g ürün → fiyat * 4 = 1 kg fiyat
        
        DataProcessor'dan gelen unit_price, unit_type ve unit_value bilgilerini korur.
        """
        for p in products:
            gramaj = p.get("gramaj")
            price = p.get("price", 0)
            
            # DataProcessor'dan gelen unit_price varsa koru, yoksa hesapla (100 birim başına)
            if "unit_price" not in p or p.get("unit_price") is None:
                if gramaj and gramaj > 0 and price > 0:
                    p["unit_price"] = round((price / gramaj) * 100, 2)
                    p["unit_price_per_100"] = p["unit_price"]
            
            # Normalized price per kg hesapla (1 kg başına) - her zaman hesapla
            if gramaj and gramaj > 0 and price > 0:
                p["normalized_price_per_kg"] = round((price / gramaj) * 1000, 2)
            else:
                p["normalized_price_per_kg"] = None
            
            # DataProcessor'dan gelen unit_type ve unit_value bilgilerini koru (varsa)
            # Bu bilgiler zaten üründe olmalı, sadece emin olmak için kontrol ediyoruz
        return products

    # ── 1e. AI Re-Ranking (Gemini) ───────────────────────────────────────────

    async def _ai_rerank(
        self,
        query: str,
        products: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Gemini'ye filtrelenmiş listeyi gönderir; alakasız olanları çıkarıp
        1 kg fiyatına göre sıralamasını ister.
        Hata veya kota doluysa mevcut listeyi olduğu gibi döndürür.
        """
        if not products:
            return products

        try:
            reranked = await self._ai.rerank_products(query, products)
            if reranked and isinstance(reranked, list) and len(reranked) > 0:
                logger.info(
                    "[FilterService] AI re-rank: %d → %d ürün.",
                    len(products), len(reranked),
                )
                return reranked
        except Exception as exc:
            logger.warning("[FilterService] AI re-rank başarısız (fallback): %s", exc)

        return products
