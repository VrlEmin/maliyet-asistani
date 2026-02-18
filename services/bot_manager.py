"""
BotManager – Tüm market scraper'larını asyncio.gather ile paralel çalıştıran orkestratör.

Migros, BİM, A101, ŞOK ve Tarım Kredi botlarını aynı anda tetikler;
sonuçları ortak şablona (Ürün Adı, Fiyat, Gramaj) oturtup döndürür.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from redis.asyncio import Redis

from scrapers import (
    AbstractBaseScraper,
    MigrosScraper,
    BimScraper,
    A101Scraper,
    SokScraper,
    TarimKrediScraper,
    TekelScraper,
)

logger = logging.getLogger(__name__)

# Arama terimi genişletme: "tavuk göğüsü" aranırken "tavuk bonfile" ve "piliç bonfile" da aranır (marketler bazen bu ifadeleri kullanır)
QUERY_ALIASES: dict[str, list[str]] = {
    "tavuk göğüsü": ["tavuk göğüsü", "tavuk bonfile", "piliç bonfile"],
    "tavuk gogusu": ["tavuk göğüsü", "tavuk bonfile", "piliç bonfile"],
}


def _expand_search_terms(query: str) -> list[str]:
    """Kullanıcı sorgusunu normalize edip gerekirse alternatif terimlerle genişletir (örn. tavuk göğüsü → tavuk bonfile dahil)."""
    q = (query or "").strip().lower()
    # Türkçe karakterleri normalize et (göğüsü -> gogusu gibi)
    q_normalized = q.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
    for key, terms in QUERY_ALIASES.items():
        key_norm = key.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
        if key_norm == q_normalized or key == q or key_norm in q_normalized:
            return list(terms)
    return [query.strip()] if query else []


def _parse_gramaj(text: str) -> float | None:
    """Ürün adından gramaj çıkarır (gram cinsinden)."""
    if not text:
        return None
    text = text.replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:gr?|gram|g)\b", text, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*kg\b", text, re.I)
    if m:
        return float(m.group(1)) * 1000
    return None


def _standardize_product(item: dict[str, Any]) -> dict[str, Any]:
    """Ham bot çıktısını ortak şablona çeker; fiyat TL ve 2 ondalık (örn. 189.95)."""
    gramaj = item.get("gramaj")
    if gramaj is None:
        gramaj = _parse_gramaj(item.get("product_name") or "")
    price = round(float(item.get("price", 0)), 2)
    # Birim fiyat: 1 kg fiyatı (gramaj varsa)
    unit_price = None
    if gramaj and gramaj > 0 and price > 0:
        unit_price = round((price / gramaj) * 1000, 2)  # TL/kg
    return {
        "product_name": item.get("product_name", ""),
        "price": price,
        "gramaj": gramaj,
        "unit_price_per_kg": unit_price,
        "market_name": item.get("market_name", ""),
        "currency": item.get("currency", "TRY"),
        "image_url": item.get("image_url"),
    }


class BotManager:
    """Market botlarını yöneten orkestratör sınıfı."""

    def __init__(self, redis_client: Redis) -> None:
        self.redis = redis_client
        self._scrapers: list[AbstractBaseScraper] = [
            MigrosScraper(redis_client),
            # BimScraper(redis_client),  # Askıya alındı - veri kalitesi sorunları nedeniyle
            A101Scraper(redis_client),
            SokScraper(redis_client),
            TarimKrediScraper(redis_client),
            TekelScraper(redis_client),
        ]

    @property
    def scraper_names(self) -> list[str]:
        return [s.MARKET_NAME for s in self._scrapers]

    # ── Paralel Arama ────────────────────────────────────────────────────────

    BOT_TIMEOUT_SECONDS = 25.0  # Tarım Kredi için daha uzun timeout

    async def _search_with_timeout(self, scraper: AbstractBaseScraper, query: str) -> list[dict[str, Any]] | Exception:
        """Tek bir botu en fazla BOT_TIMEOUT_SECONDS süreyle çalıştırır; aşarsa beklemeyi bırakır."""
        try:
            return await asyncio.wait_for(
                scraper.search_product(query),
                timeout=self.BOT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as e:
            logger.warning("[BotManager] %s zaman aşımı (%s sn)", scraper.MARKET_NAME, self.BOT_TIMEOUT_SECONDS)
            return e
        except Exception as e:
            return e

    async def search_all(self, query: str, standardize: bool = True) -> dict[str, Any]:
        """
        Tüm market botlarını aynı anda çalıştırır (asyncio.gather).
        'tavuk göğüsü' gibi sorgularda 'tavuk bonfile' da aranır ve sonuçlar birleştirilir.
        Bir bot 25 saniyeyi aşarsa beklenmez, diğer marketlerin sonuçları döner.

        Returns:
            query, results (standart şablonda: product_name, price TL 2 ondalık, gramaj, market_name, ...),
            cheapest, most_expensive, potential_saving.
        """
        search_terms = _expand_search_terms(query)
        # Her (scraper, term) için paralel görev
        tasks: list[tuple[AbstractBaseScraper, str, asyncio.Task]] = []
        for scraper in self._scrapers:
            for term in search_terms:
                tasks.append((scraper, term, asyncio.create_task(self._search_with_timeout(scraper, term))))

        # Tüm görevleri paralel çalıştır (concurrency)
        raw_results = await asyncio.gather(*[t[2] for t in tasks], return_exceptions=True)

        all_products: list[dict[str, Any]] = []
        # Deduplication: (market_name, product_name, price) bazında tekilleştirme
        seen: set[tuple[str, str, float]] = set()
        markets_responded: list[str] = []
        markets_failed: list[tuple[str, str]] = []

        for (scraper, term), result in zip([(t[0], t[1]) for t in tasks], raw_results):
            # Error Handling: Hata durumunda diğer marketleri etkileme
            if isinstance(result, Exception):
                error_msg = str(result)[:200]
                logger.error("[BotManager] %s hatası (%s): %s", scraper.MARKET_NAME, term, error_msg)
                markets_failed.append((scraper.MARKET_NAME, error_msg))
                continue
            
            if isinstance(result, list):
                n_added = 0
                sok_before = len([p for p in all_products if p.get("market_name") == "ŞOK"])
                
                # Aynı marketten gelen mükerrer ürünleri temizle
                for p in result:
                    product_name = (p.get("product_name") or "").strip()
                    market_name = p.get("market_name") or ""
                    price = round(float(p.get("price", 0)), 2)
                    
                    # Deduplication key: (market_name, product_name, price)
                    dedup_key = (market_name, product_name, price)
                    if dedup_key in seen:
                        continue
                    
                    seen.add(dedup_key)
                    all_products.append(p)
                    n_added += 1
                
                sok_after = len([p for p in all_products if p.get("market_name") == "ŞOK"])
                if scraper.MARKET_NAME == "ŞOK":
                    logger.info("[BotManager] ŞOK botu '%s' için %d ürün döndürdü, toplam ŞOK ürünü: %d", term, n_added, sok_after)
                    if n_added > 0:
                        sample_names = [p.get("product_name", "")[:50] for p in result[:3]]
                        logger.info("[BotManager] ŞOK örnek ürün adları: %s", sample_names)
                if n_added > 0:
                    markets_responded.append(f"{scraper.MARKET_NAME}(+{n_added})")

        logger.info(
            "[BotManager] Sorgu '%s' (terimler: %s) – Toplam %d ürün. Hata alan: %s",
            query,
            search_terms,
            len(all_products),
            [m[0] for m in markets_failed] if markets_failed else "yok",
        )
        for name, err in markets_failed:
            logger.debug("[BotManager] %s nedeni: %s", name, err)

        if standardize:
            all_products = [_standardize_product(p) for p in all_products]

        # Sorting: Fiyata göre artan sırada sırala (ucuzdan pahalıya)
        all_products.sort(key=lambda x: (float(x.get("price", 0)), x.get("product_name", "")))

        return {
            "query": query,
            "results": all_products,
        }

    async def search_all_markets(self, query: str) -> dict[str, Any]:
        """
        Global Search Metodu: Tüm marketlerden arama yapar.
        
        Bu metod search_all() metodunu wrap eder ve daha kullanıcı dostu bir arayüz sağlar.
        Tüm marketlerden paralel olarak veri çeker, mükerrer ürünleri temizler,
        fiyata göre sıralar ve hata durumlarını yönetir.
        
        Args:
            query: Aranacak ürün adı
            
        Returns:
            {
                "query": str,
                "results": list[dict],  # Fiyata göre sıralanmış, tekilleştirilmiş ürünler
                "markets_responded": list[str],  # Başarılı marketler
                "markets_failed": list[tuple[str, str]],  # Hata alan marketler (market_name, error)
                "total_products": int,
            }
        """
        result = await self.search_all(query, standardize=True)
        
        # Market istatistiklerini hesapla
        markets_responded = set()
        markets_failed = []
        
        for product in result["results"]:
            market_name = product.get("market_name", "")
            if market_name:
                markets_responded.add(market_name)
        
        # Hata loglarından market isimlerini çıkar (eğer varsa)
        # Bu bilgi search_all içinde loglanıyor ama döndürülmüyor
        # Bu yüzden sadece başarılı marketleri döndürüyoruz
        
        return {
            "query": result["query"],
            "results": result["results"],
            "markets_responded": sorted(list(markets_responded)),
            "total_products": len(result["results"]),
        }

    # ── Sepet (çoklu ürün) araması ───────────────────────────────────────────

    async def search_basket(self, queries: list[str]) -> dict[str, Any]:
        """
        Virgülle ayrılmış ürün listesi için her ürünü paralel arar; sonuçları toplar.

        Args:
            queries: Örn. ["süt", "yumurta", "peynir"]

        Returns:
            {
                "queries": list[str],
                "per_product": { "süt": { "results": [...], "total_products": N }, ... }
            }
        """
        if not queries:
            return {"queries": [], "per_product": {}}
        tasks = [self.search_all_markets(q) for q in queries]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        per_product: dict[str, dict[str, Any]] = {}
        for q, result in zip(queries, raw_results):
            if isinstance(result, Exception):
                logger.warning("[BotManager] search_basket '%s' hatası: %s", q, result)
                per_product[q] = {"results": [], "total_products": 0}
                continue
            per_product[q] = {
                "results": result.get("results", []),
                "total_products": result.get("total_products", 0),
            }
        logger.info(
            "[BotManager] search_basket: %d ürün, toplam %d kayıt",
            len(queries),
            sum(p["total_products"] for p in per_product.values()),
        )
        return {"queries": list(queries), "per_product": per_product}

    # ── Belirli Bir Ürünün Fiyatlarını Topla ─────────────────────────────────

    async def get_price_from_all(self, product_id: str) -> list[dict[str, Any]]:
        """
        Belirli bir ürün ID'si ile tüm marketlerden fiyat toplar.
        """
        tasks = [scraper.get_product_price(product_id) for scraper in self._scrapers]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        prices: list[dict[str, Any]] = []
        for scraper, result in zip(self._scrapers, raw_results):
            if isinstance(result, Exception):
                logger.error(
                    "[BotManager] %s fiyat hatası: %s",
                    scraper.MARKET_NAME,
                    result,
                )
                continue
            if result is not None:
                prices.append(result)

        prices.sort(key=lambda x: x.get("price", float("inf")))
        return prices

    # ── Yaşam Döngüsü ───────────────────────────────────────────────────────

    async def close(self) -> None:
        """Tüm scraper'ların HTTP istemcilerini kapatır."""
        close_tasks = [scraper.close() for scraper in self._scrapers]
        await asyncio.gather(*close_tasks, return_exceptions=True)
        logger.info("[BotManager] Tüm scraper bağlantıları kapatıldı.")
