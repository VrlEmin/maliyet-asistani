"""
Veritabanı Fiyat Analizi – maliyet-db (PostgreSQL) üzerinde prices tablosu incelemesi.

Plan: Son 10 kayıt (JOIN ile product_name, market_name), anomali kontrolü,
market bazlı veri dağılımı. Proje kökü backend kabul edilir (PYTHONPATH=backend veya backend içinden .).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime

from sqlalchemy import func, select

from app.db.session import async_session
from app.models import Market, Price, Product

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
logger = logging.getLogger(__name__)

# Anomali eşikleri (ileride kategori bazlı ortalama ile geliştirilebilir)
MIN_PRICE_THRESHOLD = 0.01  # TL altı şüpheli düşük
MAX_AVG_RATIO = 10.0  # max/ortalama oranı bu değeri aşarsa şüpheli yüksek
MIN_PRODUCT_NAME_LEN = 2  # Ürün adı bu uzunluktan kısaysa eksik/yanlış eşleşme


async def fetch_last_10(session):
    """
    prices + products + markets JOIN ile son 10 kaydı getirir.
    Şema: prices.id, prices.price, prices.scraped_at, products.name (product_name), markets.name (market_name).
    """
    stmt = (
        select(
            Price.id,
            Price.price,
            Price.scraped_at,
            Product.name.label("product_name"),
            Market.name.label("market_name"),
        )
        .select_from(Price)
        .join(Product, Price.product_id == Product.id)
        .join(Market, Price.market_id == Market.id)
        .order_by(Price.scraped_at.desc().nullslast(), Price.id.desc())
        .limit(10)
    )
    result = await session.execute(stmt)
    return result.mappings().all()


async def fetch_market_distribution(session):
    """Market bazlı kayıt sayısı (GROUP BY markets.name)."""
    stmt = (
        select(Market.name.label("market_name"), func.count(Price.id).label("count"))
        .select_from(Price)
        .join(Market, Price.market_id == Market.id)
        .group_by(Market.name)
        .order_by(func.count(Price.id).desc())
    )
    result = await session.execute(stmt)
    return result.mappings().all()


def check_anomalies(rows: list[dict]) -> list[str]:
    """
    Son 10 kayıt üzerinde anomali kontrolü:
    - Fiyat <= 0 veya çok düşük (MIN_PRICE_THRESHOLD)
    - Fiyat > ortalama * MAX_AVG_RATIO (aşırı yüksek)
    - Ürün adı boş veya çok kısa (MIN_PRODUCT_NAME_LEN)
    """
    anomalies: list[str] = []
    if not rows:
        return anomalies

    prices = [float(r["price"]) for r in rows]
    avg_price = sum(prices) / len(prices) if prices else 0.0

    for r in rows:
        row_id = r["id"]
        price = float(r["price"])
        product_name = (r["product_name"] or "").strip()
        market_name = r["market_name"] or ""

        if price < 0 or (price >= 0 and price < MIN_PRICE_THRESHOLD):
            anomalies.append(
                f"  [id={row_id}] Şüpheli düşük fiyat: {price:.2f} TL (ürün: {product_name[:40] or '—'}..., market: {market_name})"
            )
        elif avg_price > 0 and price > avg_price * MAX_AVG_RATIO:
            anomalies.append(
                f"  [id={row_id}] Şüpheli yüksek fiyat: {price:.2f} TL (ortalama≈{avg_price:.2f} TL, oran>{MAX_AVG_RATIO}x) (ürün: {product_name[:40] or '—'}..., market: {market_name})"
            )
        if len(product_name) < MIN_PRODUCT_NAME_LEN:
            anomalies.append(
                f"  [id={row_id}] Eksik/kısa ürün adı: '{product_name or '(boş)'}' (market: {market_name})"
            )

    return anomalies


def format_ts(ts):
    """scraped_at için okunabilir tarih."""
    if ts is None:
        return "—"
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m-%d %H:%M")
    return str(ts)


async def main() -> None:
    print("--- Veritabanı Fiyat Analizi (maliyet-db) ---\n")

    try:
        async with async_session() as session:
            # Son 10 kayıt (JOIN)
            rows = await fetch_last_10(session)
            if not rows:
                print("Veri bulunamadı.")
                print("\nNot: Scraper verileri şu an Redis'te tutuluyor; PostgreSQL prices tablosu boş olabilir.")
                print("Kalıcı kayıt için 'Data Pipeline' servisi yazılmalı.")
                return

            # Tablo: son 10
            print("--- Son 10 kayıt (prices + product_name + market) ---")
            print(f"{'id':<8} {'product_name':<42} {'price':>10} {'market':<14} scraped_at")
            print("-" * 95)
            for r in rows:
                pid = r["id"]
                pname = (r["product_name"] or "")[:40]
                price = float(r["price"])
                mname = (r["market_name"] or "")[:12]
                ts = format_ts(r["scraped_at"])
                print(f"{pid:<8} {pname:<42} {price:>10.2f} {mname:<14} {ts}")
            print()

            # Anomali kontrolü
            print("--- Anomali kontrolü ---")
            row_dicts = [dict(r) for r in rows]
            anomalies = check_anomalies(row_dicts)
            if anomalies:
                for a in anomalies:
                    print(a)
            else:
                print("  Tespit edilen anomali yok.")
            print()

            # Market bazlı dağılım (tüm tablo)
            dist = await fetch_market_distribution(session)
            print("--- Market bazlı veri dağılımı ---")
            if not dist:
                print("  Veri yok.")
            else:
                total = 0
                for d in dist:
                    cnt = d["count"]
                    total += cnt
                    print(f"  {d['market_name']}: {cnt}")
                print(f"  Toplam: {total} kayıt.")

    except Exception as e:
        logger.exception("Veritabanı hatası: %s", e)
        print("\nBağlantı/sorgu hatası. .env içinde DATABASE_URL (veya KULLANICI_ADI, VERITABANI_ADI, SIFRE) kontrol edin.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
