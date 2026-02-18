"""
AbstractBaseScraper – Tüm market botlarının türediği soyut temel sınıf.

Ortak işlevler:
  • Redis cache okuma / yazma
  • httpx ile async HTTP istekleri (retry + rate-limit)
  • Standart hata yönetimi
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx
from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class AbstractBaseScraper(ABC):
    """Market scraper'larının ana sınıfı."""

    # Alt sınıflar bu değeri kendi market adlarıyla override eder
    MARKET_NAME: str = "unknown"

    # Eşzamanlı istek sınırı (rate-limit)
    MAX_CONCURRENT_REQUESTS: int = 5

    # Cache TTL (saniye) – varsayılan 1 saat
    CACHE_TTL: int = 3600

    def __init__(self, redis_client: Redis) -> None:
        self.redis = redis_client
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_REQUESTS)
        self._client: Optional[httpx.AsyncClient] = None

    # ── HTTP Client Yaşam Döngüsü ────────────────────────────────────────────

    # Gerçekçi tarayıcı bilgileri – bot engelini azaltmak için (A101, BİM vb.)
    DEFAULT_HEADERS: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    async def _get_client(self) -> httpx.AsyncClient:
        """Tembel başlatma ile httpx.AsyncClient döndürür (gerçekçi tarayıcı header'ları ile)."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
                headers=dict(self.DEFAULT_HEADERS),
            )
        return self._client

    async def close(self) -> None:
        """HTTP istemcisini temiz şekilde kapatır."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ── Cache Yardımcıları ────────────────────────────────────────────────────

    def _cache_key(self, suffix: str) -> str:
        """Market adı ön ekli cache anahtarı üretir."""
        return f"scraper:{self.MARKET_NAME}:{suffix}"

    async def _get_cached(self, key: str) -> Optional[Any]:
        """Redis'ten cache değeri okur (JSON deserialize)."""
        try:
            raw = await self.redis.get(self._cache_key(key))
            if raw:
                return json.loads(raw)
        except Exception as exc:
            logger.warning("Cache okuma hatası [%s]: %s", key, exc)
        return None

    async def _set_cache(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Redis'e cache değeri yazar (JSON serialize)."""
        try:
            await self.redis.set(
                self._cache_key(key),
                json.dumps(value, ensure_ascii=False, default=str),
                ex=ttl or self.CACHE_TTL,
            )
        except Exception as exc:
            logger.warning("Cache yazma hatası [%s]: %s", key, exc)

    # ── HTTP İstekleri (Retry + Rate-Limit) ──────────────────────────────────

    async def _make_request(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
    ) -> httpx.Response:
        """
        Rate-limit'li ve retry mekanizmalı async HTTP isteği.

        Raises:
            httpx.HTTPStatusError: Tüm denemeler başarısız olursa.
        """
        client = await self._get_client()
        last_exc: Exception | None = None

        for attempt in range(1, max_retries + 1):
            async with self._semaphore:
                try:
                    response = await client.request(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json_body,
                    )
                    response.raise_for_status()
                    return response
                except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                    last_exc = exc
                    wait = backoff_factor * (2 ** (attempt - 1))
                    logger.warning(
                        "[%s] İstek hatası (deneme %d/%d): %s – %.1fs bekleniyor",
                        self.MARKET_NAME,
                        attempt,
                        max_retries,
                        exc,
                        wait,
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(wait)

        raise last_exc  # type: ignore[misc]

    # ── Abstract Metodlar (Alt sınıflar MUTLAKA implement etmeli) ────────────

    @abstractmethod
    async def search_product(self, query: str) -> list[dict[str, Any]]:
        """
        Ürün adına göre arama yapar.

        Returns:
            Her eleman şu anahtarları içermeli:
            {
                "product_name": str,
                "price": float,
                "currency": "TRY",
                "image_url": str | None,
                "market_name": str,
            }
        """
        ...

    @abstractmethod
    async def get_product_price(self, product_id: str) -> dict[str, Any] | None:
        """
        Belirli bir ürünün güncel fiyatını döndürür.

        Returns:
            {"product_name": str, "price": float, ...} veya None
        """
        ...
