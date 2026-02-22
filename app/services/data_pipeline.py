"""
Data Pipeline – Redis'teki market_prices:* anahtarlarındaki JSON verilerini
Gemini 2.5 Flash ile temizleyip PostgreSQL (Market, Product, Price) tablolarına yazar.

Veri önce AI servisinden geçirilir (standardize isim + kategori); temizlenmiş hali kaydedilir.
Kritik: Aynı isimde market veya ürün varsa yenisi oluşturulmaz, mevcut id kullanılır.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Market, Price, Product

logger = logging.getLogger(__name__)

# Redis'te okunacak anahtar öneki
REDIS_KEY_PREFIX = "market_prices:"


def _normalize_name(s: str | None, max_len: int = 300) -> str:
    """Boş ve fazla uzun isimleri güvenli hale getirir."""
    if not s or not isinstance(s, str):
        return ""
    return (s.strip() or "")[:max_len]


async def _get_or_create_market(session: AsyncSession, name: str) -> int:
    """Market adına göre mevcut kaydı döndürür veya yeni oluşturur. Mevcut id kullanılır."""
    name = _normalize_name(name, max_len=100)
    if not name:
        raise ValueError("Market adı boş olamaz.")
    result = await session.execute(select(Market.id).where(Market.name == name).limit(1))
    row = result.scalar_one_or_none()
    if row is not None:
        return int(row)
    market = Market(name=name)
    session.add(market)
    await session.flush()
    return market.id


async def _get_or_create_product(
    session: AsyncSession,
    name: str,
    category: str | None = None,
) -> int:
    """Ürün adına göre mevcut kaydı döndürür veya yeni oluşturur. Mevcut id kullanılır. Yeni kayıtta category set edilir."""
    name = _normalize_name(name, max_len=300)
    if not name:
        raise ValueError("Ürün adı boş olamaz.")
    result = await session.execute(select(Product.id).where(Product.name == name).limit(1))
    row = result.scalar_one_or_none()
    if row is not None:
        return int(row)
    product = Product(name=name, category=(category or "").strip()[:100] or None)
    session.add(product)
    await session.flush()
    return product.id


def _parse_redis_value(raw: str) -> list[dict[str, Any]]:
    """Redis'ten gelen JSON değerini ürün listesine çevirir."""
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as e:
        logger.warning("Redis JSON parse hatası: %s", e)
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Tek kayıt veya {"results": [...]} formatı
        if "results" in data:
            return data["results"] if isinstance(data["results"], list) else []
        return [data]
    return []


def _extract_record(item: dict[str, Any]) -> tuple[str, str, float] | None:
    """Dict'ten (product_name, market_name, price) çıkarır. Geçersizse None."""
    product_name = item.get("product_name") or item.get("name") or item.get("productName")
    market_name = item.get("market_name") or item.get("market") or item.get("marketName")
    price_raw = item.get("price")
    if not product_name or not market_name:
        return None
    try:
        price = float(price_raw)
    except (TypeError, ValueError):
        return None
    if price < 0:
        return None
    return (
        _normalize_name(str(product_name), 300),
        _normalize_name(str(market_name), 100),
        round(price, 2),
    )


async def sync_redis_to_db(
    redis_client: Redis,
    session: AsyncSession,
    *,
    key_pattern: str = f"{REDIS_KEY_PREFIX}*",
    normalizer: Any | None = None,
) -> dict[str, int]:
    """
    Redis'teki market_prices:* anahtarlarını okur, AI ile temizler (standardize + kategori),
    sonra Market/Product/Price tablolarına yazar. Aynı isimde market/ürün varsa mevcut id kullanılır.

    normalizer: ProductNormalizerService instance. Verilmezse oluşturulur; API yoksa ham isimle kaydedilir.
    """
    stats = {"keys_read": 0, "records_processed": 0, "prices_inserted": 0, "errors": 0}

    if normalizer is None:
        from app.services.ai_service import ProductNormalizerService
        normalizer = ProductNormalizerService()

    # 1) Tüm anahtarları ve kayıtları topla
    cursor = 0
    keys: list[str] = []
    while True:
        cursor, batch = await redis_client.scan(cursor=cursor, match=key_pattern, count=100)
        keys.extend(batch)
        if cursor == 0:
            break

    raw_records: list[tuple[str, str, float]] = []
    for key in keys:
        stats["keys_read"] += 1
        raw = await redis_client.get(key)
        if not raw:
            continue
        items = _parse_redis_value(raw)
        for item in items:
            rec = _extract_record(item)
            if not rec:
                continue
            raw_records.append(rec)

    if not raw_records:
        return stats

    # 2) Benzersiz ürün isimlerini AI ile 10'arlı batch halinde standardize et
    unique_names = list(dict.fromkeys(r[0] for r in raw_records))
    try:
        normalized_list = await normalizer.normalize_products(unique_names, batch_size=10)
    except Exception as e:
        logger.warning("[DataPipeline] AI normalizasyon hatası, ham isimle devam: %s", e)
        normalized_list = [
            {"original": n, "standard_name": (n or "").strip()[:300], "category": None}
            for n in unique_names
        ]
    clean_map: dict[str, tuple[str, str | None]] = {
        r["original"]: (r.get("standard_name") or r.get("original") or "", r.get("category"))
        for r in normalized_list
        if isinstance(r, dict)
    }
    for n in unique_names:
        if n not in clean_map:
            clean_map[n] = ((n or "").strip()[:300], None)

    # 3) Temizlenmiş kayıtları veritabanına yaz
    for product_name, market_name, price in raw_records:
        stats["records_processed"] += 1
        standard_name, category = clean_map.get(product_name, (product_name, None))
        if not standard_name:
            standard_name = _normalize_name(product_name, 300)
        savepoint = session.begin_nested()
        try:
            market_id = await _get_or_create_market(session, market_name)
            product_id = await _get_or_create_product(session, standard_name, category=category)
            price_row = Price(
                product_id=product_id,
                market_id=market_id,
                price=price,
                currency="TRY",
            )
            session.add(price_row)
            await session.flush()
            stats["prices_inserted"] += 1
            savepoint.commit()
        except IntegrityError as e:
            savepoint.rollback()
            logger.debug("IntegrityError (mevcut id ile tekrar denenecek): %s", e)
            try:
                result_m = await session.execute(select(Market.id).where(Market.name == market_name).limit(1))
                result_p = await session.execute(select(Product.id).where(Product.name == standard_name).limit(1))
                mid = result_m.scalar_one_or_none()
                pid = result_p.scalar_one_or_none()
                if mid is not None and pid is not None:
                    price_row = Price(product_id=int(pid), market_id=int(mid), price=price, currency="TRY")
                    session.add(price_row)
                    await session.flush()
                    stats["prices_inserted"] += 1
                else:
                    stats["errors"] += 1
            except Exception:
                stats["errors"] += 1
        except ValueError as e:
            savepoint.rollback()
            logger.debug("Skip record (value error): %s", e)
            stats["errors"] += 1
        except Exception as e:
            savepoint.rollback()
            logger.warning("Kayıt yazılırken hata: %s", e)
            stats["errors"] += 1

    return stats


async def run_pipeline(
    redis_client: Redis,
    session: AsyncSession,
    *,
    normalizer: Any | None = None,
) -> dict[str, int]:
    """
    Pipeline'ı tek seferlik çalıştırır (Redis -> AI temizleme -> DB).
    Çağıran commit veya rollback yapmalıdır.
    """
    return await sync_redis_to_db(redis_client, session, normalizer=normalizer)
