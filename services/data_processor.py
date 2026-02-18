"""
DataProcessor – BotManager'dan gelen ham sonuçları işleyen servis.

Özellikler:
1. Advanced Extraction: Ürün adlarından gramaj ve birim bilgilerini ayıklar (kg, g, lt, ml, adet, rulo, tablet, yıkama)
2. Unit Price Calculation: Birim tipine göre fiyat hesaplar (100 birim veya 1 adet)
3. Smart Ranking: unit_price değerine göre sıralar (en ekonomik olandan başlayarak)
4. Smart Filtering: Aynı marketten gelen mükerrer kayıtları temizler (%95 benzerlik)
5. Missing Data Management: Birim tespit edilemeyen ürünleri listenin sonuna atar
"""

from __future__ import annotations

import logging
import re
from typing import Any
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


class DataProcessor:
    """BotManager'dan gelen sonuçları normalize eden, birim fiyat hesaplayan ve sıralayan servis."""

    # Birim pattern'leri ve dönüşüm faktörleri
    # Format: (pattern, conversion_factor, is_countable)
    # is_countable: True ise 1 adet fiyatı, False ise 100 birim fiyatı hesaplanır
    UNIT_PATTERNS = {
        # Ağırlık birimleri (100 birim fiyatı)
        "kg": (r"(\d+(?:[.,]\d+)?)\s*(?:kg|kilogram|kilo)\b", 1000.0, False),
        "g": (r"(\d+(?:[.,]\d+)?)\s*(?:gr?|gram|g)\b", 1.0, False),
        # Hacim birimleri (100 birim fiyatı)
        "lt": (r"(\d+(?:[.,]\d+)?)\s*(?:lt|l|litre|liter)\b", 1000.0, False),  # 1 litre = 1000 ml
        "ml": (r"(\d+(?:[.,]\d+)?)\s*(?:ml|mililitre|milliliter)\b", 1.0, False),
        # Sayılabilir birimler (1 adet fiyatı)
        # Türkçe formatlar: "8'li", "16'lı", "24'lü", "60'lı" gibi
        "adet": (
            r"(\d+(?:[.,]\d+)?)\s*(?:['']?li|['']?lı|['']?lu|['']?lü|adet|ad|pcs|piece|paket|pkt)\b",
            1.0,
            True,
        ),
        "rulo": (r"(\d+(?:[.,]\d+)?)\s*(?:rulo|roll)\b", 1.0, True),
        "tablet": (r"(\d+(?:[.,]\d+)?)\s*(?:tablet|tb|tab)\b", 1.0, True),
        "yıkama": (r"(\d+(?:[.,]\d+)?)\s*(?:yıkama|yikama|wash)\b", 1.0, True),
    }

    def __init__(self) -> None:
        """DataProcessor servisini başlatır."""
        pass

    # ── Normalization ────────────────────────────────────────────────────────────

    def normalize_product(self, product: dict[str, Any]) -> dict[str, Any]:
        """
        Ürün adından gramaj ve birim bilgilerini ayıklar ve normalize eder (Advanced Extraction).
        
        Desteklenen birimler:
        - Ağırlık: kg, g
        - Hacim: lt, ml
        - Sayılabilir: adet, rulo, tablet, yıkama
        
        Args:
            product: Ham ürün verisi
            
        Returns:
            Normalize edilmiş ürün verisi (gramaj, unit_type, unit_value, is_countable alanları eklenir)
        """
        product_name = product.get("product_name", "")
        if not product_name:
            return product

        # Mevcut gramaj varsa kullan
        gramaj = product.get("gramaj")
        unit_type = product.get("unit_type", "unknown")
        unit_value = product.get("unit_value")
        is_countable = product.get("is_countable", False)

        # Ürün adından birim bilgilerini ayıkla
        text = product_name.replace(",", ".")
        text_lower = text.lower()

        # Öncelik sırası: kg > g > lt > ml > adet > rulo > tablet > yıkama
        for unit_name, (pattern, conversion_factor, countable) in self.UNIT_PATTERNS.items():
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                value_str = match.group(1).replace(",", ".")
                try:
                    value = float(value_str)
                    unit_value = value
                    unit_type = unit_name
                    is_countable = countable
                    
                    # Gramaj hesaplama (tüm birimleri gram/ml cinsine çevir)
                    if unit_name in ["kg", "g"]:
                        gramaj = value * conversion_factor
                    elif unit_name in ["lt", "ml"]:
                        # Sıvı ürünler için ml cinsinden gramaj (1 ml ≈ 1 g su için)
                        gramaj = value * conversion_factor
                    elif countable:
                        # Sayılabilir birimler için gramaj yok (rulo, adet, tablet, yıkama)
                        gramaj = None
                    
                    # İlk eşleşmeyi kullan (en spesifik olan)
                    break
                except (ValueError, TypeError):
                    continue

        # Sonuçları ürüne ekle
        result = dict(product)
        result["gramaj"] = gramaj
        result["unit_type"] = unit_type
        result["unit_value"] = unit_value
        result["is_countable"] = is_countable
        result["has_unit_info"] = unit_type != "unknown" and unit_value is not None

        return result

    # ── Unit Price Calculation ───────────────────────────────────────────────────

    def calculate_unit_price(self, product: dict[str, Any]) -> dict[str, Any]:
        """
        Birim tipine göre fiyat hesaplar ve unit_price alanına ekler.
        
        Hesaplama mantığı:
        - g veya ml ise: (price / amount) * 100 (100 birim fiyatı)
        - rulo, adet, tablet, yıkama ise: price / amount (1 adet fiyatı)
        
        Args:
            product: Normalize edilmiş ürün verisi
            
        Returns:
            unit_price alanı eklenmiş ürün verisi
        """
        price = product.get("price", 0)
        gramaj = product.get("gramaj")
        unit_type = product.get("unit_type", "unknown")
        unit_value = product.get("unit_value")
        is_countable = product.get("is_countable", False)

        unit_price = None
        unit_price_note = None

        if price and price > 0:
            if is_countable and unit_value and unit_value > 0:
                # Sayılabilir birimler için: 1 adet fiyatı
                # Örn: 32 Rulo Tuvalet Kağıdı 50 TL -> unit_price = 50/32 = 1.56 TL/rulo
                unit_price = round(price / unit_value, 2)
                unit_price_note = f"1 {unit_type} fiyatı"
            elif gramaj and gramaj > 0:
                # Ağırlık/hacim birimleri için: 100 birim başına fiyat
                # Örn: 1 L Süt 27.50 TL -> unit_price = (27.50 / 1000) * 100 = 2.75 TL/100ml
                unit_price = round((price / gramaj) * 100, 2)
                unit_price_note = f"100 {unit_type} fiyatı"
            else:
                # Birim bilgisi yoksa unit_price hesaplanamaz
                unit_price = None
                unit_price_note = "Birim fiyat hesaplanamadı"

        result = dict(product)
        result["unit_price"] = unit_price
        result["unit_price_per_100"] = unit_price  # Geriye uyumluluk için
        result["unit_price_note"] = unit_price_note

        return result

    # ── Filtering ────────────────────────────────────────────────────────────────

    def filter_invalid_products(self, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Hatalı sonuçları temizler.
        
        Filtrelenen ürünler:
        - Fiyatı 0 veya negatif olanlar
        - Fiyatı None olanlar
        - Ürün adı boş olanlar
        
        Args:
            products: Ürün listesi
            
        Returns:
            Filtrelenmiş ürün listesi
        """
        valid_products = []
        filtered_count = 0

        for product in products:
            price = product.get("price", 0)
            product_name = product.get("product_name", "").strip()

            # Filtreleme kriterleri
            if not product_name:
                filtered_count += 1
                logger.debug("[DataProcessor] Ürün adı boş, filtrelendi: %s", product)
                continue

            if price is None or price <= 0:
                filtered_count += 1
                logger.debug(
                    "[DataProcessor] Fiyat geçersiz (%s), filtrelendi: %s",
                    price,
                    product_name[:50],
                )
                continue

            valid_products.append(product)

        if filtered_count > 0:
            logger.info(
                "[DataProcessor] %d hatalı ürün filtrelendi (toplam: %d → %d)",
                filtered_count,
                len(products),
                len(valid_products),
            )

        return valid_products

    def smart_filter_duplicates(self, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Aynı marketten gelen, ismi ve fiyatı %95 aynı olan mükerrer kayıtları temizler.
        
        Benzerlik hesaplama:
        - Ürün adı benzerliği: SequenceMatcher kullanarak
        - Fiyat benzerliği: %5 tolerans ile
        
        Args:
            products: Ürün listesi
            
        Returns:
            Tekilleştirilmiş ürün listesi
        """
        if not products:
            return products

        # Market bazında grupla
        market_groups: dict[str, list[dict[str, Any]]] = {}
        for product in products:
            market_name = product.get("market_name", "unknown")
            if market_name not in market_groups:
                market_groups[market_name] = []
            market_groups[market_name].append(product)

        unique_products = []
        duplicate_count = 0

        for market_name, market_products in market_groups.items():
            seen_indices = set()
            
            for i, product1 in enumerate(market_products):
                if i in seen_indices:
                    continue
                
                # Bu ürünü benzersiz olarak işaretle
                unique_products.append(product1)
                
                # Diğer ürünlerle karşılaştır
                for j, product2 in enumerate(market_products[i + 1:], start=i + 1):
                    if j in seen_indices:
                        continue
                    
                    # Ürün adı benzerliği
                    name1 = product1.get("product_name", "").lower().strip()
                    name2 = product2.get("product_name", "").lower().strip()
                    name_similarity = SequenceMatcher(None, name1, name2).ratio()
                    
                    # Fiyat benzerliği (%5 tolerans)
                    price1 = product1.get("price", 0)
                    price2 = product2.get("price", 0)
                    if price1 > 0 and price2 > 0:
                        price_diff = abs(price1 - price2) / max(price1, price2)
                        price_similar = price_diff <= 0.05  # %5 tolerans
                    else:
                        price_similar = price1 == price2
                    
                    # %95 benzerlik kontrolü
                    if name_similarity >= 0.95 and price_similar:
                        seen_indices.add(j)
                        duplicate_count += 1
                        logger.debug(
                            "[DataProcessor] Mükerrer ürün filtrelendi: '%s' (benzerlik: %.2f%%, fiyat farkı: %.2f%%)",
                            name2[:50],
                            name_similarity * 100,
                            price_diff * 100 if price1 > 0 and price2 > 0 else 0,
                        )

        if duplicate_count > 0:
            logger.info(
                "[DataProcessor] Smart filtering: %d mükerrer ürün temizlendi (toplam: %d → %d)",
                duplicate_count,
                len(products),
                len(unique_products),
            )

        return unique_products

    # ── Smart Ranking ─────────────────────────────────────────────────────────────

    def smart_rank(self, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Ürünleri unit_price değerine göre sıralar (en ekonomik olandan başlayarak).
        
        Eksik Veri Yönetimi:
        - Birim bilgisi tespit edilemeyen ürünler listenin sonuna atılır
        
        Sıralama önceliği:
        1. has_unit_info (birim bilgisi olanlar önce)
        2. unit_price (100 birim veya 1 adet fiyatı) - küçükten büyüğe
        3. price (toplam fiyat) - küçükten büyüğe
        4. product_name (alfabetik)
        
        Args:
            products: Ürün listesi
            
        Returns:
            Sıralanmış ürün listesi (birim bilgisi olmayanlar sonda)
        """
        # Birim bilgisi olan ve olmayan ürünleri ayır
        with_unit_info = []
        without_unit_info = []
        
        for product in products:
            has_unit = product.get("has_unit_info", False)
            if has_unit:
                with_unit_info.append(product)
            else:
                without_unit_info.append(product)
        
        # Birim bilgisi olanları unit_price'e göre sırala
        sorted_with_unit = sorted(
            with_unit_info,
            key=lambda p: (
                p.get("unit_price") if p.get("unit_price") is not None else float("inf"),
                p.get("price", float("inf")),
                p.get("product_name", ""),
            ),
        )
        
        # Birim bilgisi olmayanları fiyata göre sırala
        sorted_without_unit = sorted(
            without_unit_info,
            key=lambda p: (
                p.get("price", float("inf")),
                p.get("product_name", ""),
            ),
        )
        
        # Birleştir: önce birim bilgisi olanlar, sonra olmayanlar
        sorted_products = sorted_with_unit + sorted_without_unit

        logger.info(
            "[DataProcessor] %d ürün sıralandı (%d birim bilgili, %d birim bilgisiz)",
            len(sorted_products),
            len(sorted_with_unit),
            len(sorted_without_unit),
        )

        return sorted_products

    # ── Ana Pipeline ──────────────────────────────────────────────────────────────

    def process(self, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Tam pipeline: Normalization → Unit Price Calculation → Filtering → Smart Filtering → Smart Ranking.
        
        Args:
            products: BotManager'dan gelen ham ürün listesi
            
        Returns:
            İşlenmiş, tekilleştirilmiş ve sıralanmış ürün listesi
        """
        count_in = len(products)

        # 1) Normalization: Ürün adlarından gramaj ve birim bilgilerini ayıkla (Advanced Extraction)
        normalized_products = [self.normalize_product(p) for p in products]
        unit_info_count = sum(1 for p in normalized_products if p.get("has_unit_info", False))
        logger.info(
            "[DataProcessor] Normalization tamamlandı: %d ürün (%d üründe birim bilgisi tespit edildi)",
            len(normalized_products),
            unit_info_count,
        )

        # 2) Unit Price Calculation: Birim tipine göre fiyat hesapla
        products_with_unit_price = [self.calculate_unit_price(p) for p in normalized_products]
        unit_price_count = sum(1 for p in products_with_unit_price if p.get("unit_price") is not None)
        logger.info(
            "[DataProcessor] Unit price hesaplama tamamlandı: %d ürün (%d üründe unit_price hesaplandı)",
            len(products_with_unit_price),
            unit_price_count,
        )

        # 3) Filtering: Hatalı sonuçları temizle
        filtered_products = self.filter_invalid_products(products_with_unit_price)
        logger.info(
            "[DataProcessor] Filtreleme tamamlandı: %d → %d ürün",
            len(products_with_unit_price),
            len(filtered_products),
        )

        # 4) Smart Filtering: Aynı marketten gelen mükerrer kayıtları temizle (%95 benzerlik)
        deduplicated_products = self.smart_filter_duplicates(filtered_products)
        logger.info(
            "[DataProcessor] Smart filtering tamamlandı: %d → %d ürün",
            len(filtered_products),
            len(deduplicated_products),
        )

        # 5) Smart Ranking: unit_price'e göre sırala (birim bilgisi olmayanlar sonda)
        ranked_products = self.smart_rank(deduplicated_products)

        logger.info(
            "[DataProcessor] Pipeline tamamlandı: %d → %d ürün (işlenmiş, tekilleştirilmiş ve sıralanmış)",
            count_in,
            len(ranked_products),
        )

        return ranked_products
