"""
AbstractBaseScraper – Tüm market botlarının türediği soyut temel sınıf.

Ortak işlevler:
  • Redis cache okuma / yazma
  • httpx ile async HTTP istekleri (retry + rate-limit)
  • Standart hata yönetimi
  • Fiyat dönüşümü (_safe_price)
  • Gramaj/birim parse (_parse_gramaj_from_name)
  • Metin temizleme / encoding düzeltme (_clean_text)
  • User-Agent ve header yardımcıları (desktop / iPhone)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Literal, Optional

import httpx
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Kuruş → TL dönüşümü (varsayılan değerler)
DEFAULT_PRICE_THRESHOLD = 1000.0
DEFAULT_PRICE_DIVISOR = 100.0

# iPhone User-Agent (A101, ŞOK, TarımKredi için bot algılamayı önlemek)
IPHONE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.6 Mobile/15E148 Safari/604.1"
)


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

    # ── Fiyat / Gramaj / Metin Yardımcıları ─────────────────────────────────

    @staticmethod
    def _safe_price(
        raw: float | int,
        threshold: float = DEFAULT_PRICE_THRESHOLD,
        divisor: float = DEFAULT_PRICE_DIVISOR,
    ) -> float:
        """
        Ham fiyatı TL'ye çevirir.
        Fiyat > threshold ise kuruş kabul edilip divisor'a bölünür; aksi hâlde TL'dir.
        Böylece cache'te kalan eski kuruş verileri de doğru işlenir.
        """
        raw_float = float(raw)
        if raw_float > threshold:
            return round(raw_float / divisor, 2)
        return round(raw_float, 2)

    @staticmethod
    def _parse_gramaj_from_name(product_name: str) -> float | None:
        """Ürün adından gramaj / birim bilgisini çıkarır (gram, kg, ml, L)."""
        if not product_name:
            return None

        text = product_name.replace(",", ".")

        # Gram (örn: "1000 G", "750 G")
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:gr?|gram|g)\b", text, re.I)
        if m:
            return float(m.group(1))

        # Kilogram (örn: "2 Kg", "1.5 Kg")
        m = re.search(r"(\d+(?:\.\d+)?)\s*kg\b", text, re.I)
        if m:
            return float(m.group(1)) * 1000

        # Mililitre/Litre (örn: "500 ml", "1 L")
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:ml|lt|l)\b", text, re.I)
        if m:
            val = float(m.group(1))
            if val < 20:  # litre
                return val * 1000
            return val  # ml

        return None

    @staticmethod
    def _clean_text(text: str) -> str:
        """
        Encoding sorunlu metinleri düzeltir.
        Örn: "SÃ¼t" -> "Süt", "MÄ±sÄ±r" -> "Mısır", "YaÄ\x9flÄ±" -> "Yağlı"
        """
        if not text:
            return text

        try:
            fixed_text = text

            # Adım 1: Özel karakter kombinasyonları
            special_combinations = {
                "YaÄ\x9fs": "Yağs",
                "YaÄ\x9fl": "Yağl",
                "Ä\x9fs": "ğs",
                "Ä\x9fl": "ğl",
                "\x9fsÄ": "ğsı",
                "\x9flÄ": "ğlı",
                "ıÇ": "Ç",
                "ıç": "ç",
                "ıĞ": "Ğ",
                "ığ": "ğ",
                "ıŞ": "Ş",
                "ış": "ş",
                "ıÜ": "Ü",
                "ıü": "ü",
                "ıÖ": "Ö",
                "ıö": "ö",
                "ıİ": "İ",
                "ı¶": "ö",
                "Åğ": "ş",
                "şğ": "ş",
                "ıÇi": "Çi",
                "ıçi": "çi",
            }
            for wrong, correct in sorted(
                special_combinations.items(), key=lambda x: -len(x[0])
            ):
                fixed_text = fixed_text.replace(wrong, correct)

            # Adım 2: Tek karakter düzeltmeleri
            single_char_fixes = {
                "Ã¼": "ü",
                "Ã§": "ç",
                "Ä±": "ı",
                "Ä°": "İ",
                "¼": "ü",
                "±": "ı",
                "\x9f": "ğ",
                "Å": "ş",
                "¶": "ö",
            }
            for wrong, correct in single_char_fixes.items():
                fixed_text = fixed_text.replace(wrong, correct)

            # Adım 3: Genel fallback
            general_fixes = {"Ã": "ı", "Ä": "ı"}
            for wrong, correct in general_fixes.items():
                fixed_text = fixed_text.replace(wrong, correct)

            # Adım 4: "ı" + Türkçe karakter düzeltmesi
            turkish_chars = "ÇĞİÖŞÜçğıöşü"
            pattern = f"ı([{turkish_chars}])"
            fixed_text = re.sub(pattern, r"\1", fixed_text)

            # Adım 5: Çift karakter
            for wrong, correct in [("şğ", "ş"), ("Şğ", "Ş")]:
                fixed_text = fixed_text.replace(wrong, correct)

            return fixed_text
        except Exception as e:
            logger.debug("Metin temizleme hatası: %s", e)
            return text

    @staticmethod
    def get_headers_for_device(
        device: Literal["desktop", "iphone"] = "desktop",
        referer: str | None = None,
        accept: str | None = None,
    ) -> dict[str, str]:
        """
        Cihaza göre header dict döndürür.
        Scraper'lar merge edip kullanır.
        """
        if device == "iphone":
            headers: dict[str, str] = {
                "User-Agent": IPHONE_USER_AGENT,
                "Accept": accept or "application/json",
            }
        else:
            headers = dict(AbstractBaseScraper.DEFAULT_HEADERS)
            if accept:
                headers["Accept"] = accept
        if referer:
            headers["Referer"] = referer
        return headers

    # ── HTTP Client / Headers ───────────────────────────────────────────────

    # Gerçekçi tarayıcı bilgileri – bot engelini azaltmak için
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

    # ── Cache Yardımcıları ──────────────────────────────────────────────────

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

    # ── HTTP İstekleri (Retry + Rate-Limit) ─────────────────────────────────

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

    # ── Abstract Metodlar (Alt sınıflar MUTLAKA implement etmeli) ───────────

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
