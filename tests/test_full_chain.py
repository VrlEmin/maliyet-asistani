"""
Uçtan uca (full-chain) test: BotManager → DataProcessor → AIService.

Bu script şunları yapar:
1. BotManager ile '5 lt ayçiçek yağı' sorgusu
2. Ham verileri DataProcessor ile işler
3. İşlenmiş verileri AIService.generate_shopping_advice'e gönderir
4. Sonuçları terminale yazdırır

Çalıştırma (backend dizininden):
    python tests/test_full_chain.py

veya Docker içinde:
    docker exec maliyet-asistani-api python tests/test_full_chain.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# backend dizinini Python path'e ekle (script her yerden çalıştırılabilsin)
_backend = Path(__file__).resolve().parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from models.database import init_redis, close_redis
from services.bot_manager import BotManager
from services.data_processor import DataProcessor
from services.ai_service import AIService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

QUERY = "5 lt ayçiçek yağı"


def _print_section(title: str, content: str | list) -> None:
    """Başlık ve içeriği terminale yazdırır."""
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  {title}")
    print(sep)
    if isinstance(content, list):
        for item in content:
            print(item)
    else:
        print(content)
    print()


async def main() -> None:
    redis_client = None
    bot_manager = None

    try:
        # Redis bağlantısı
        redis_client = await init_redis()
        await redis_client.ping()
        logger.info("Redis bağlantısı OK")

        bot_manager = BotManager(redis_client)
        data_processor = DataProcessor()
        ai_service = AIService()

        # 1. BotManager ile arama
        logger.info("Sorgu: %s - Marketlerden veri çekiliyor...", QUERY)
        raw = await bot_manager.search_all_markets(QUERY)
        ham_veriler = raw.get("results", [])

        # Marketlerden Gelen Veri
        raw_lines = []
        for i, p in enumerate(ham_veriler, 1):
            name = p.get("product_name", "")[:55]
            price = p.get("price", 0)
            market = p.get("market_name", "")
            raw_lines.append(f"  {i:2}. {name:<55} | {price:>8.2f} TL | {market}")
        if not raw_lines:
            raw_lines.append("  (Hiç ürün bulunamadı)")
        _print_section("Marketlerden Gelen Veri", raw_lines)

        # 2. DataProcessor ile işle
        logger.info("DataProcessor ile işleniyor...")
        processed = data_processor.process(ham_veriler)

        # İşlenmiş Birim Fiyatlar
        proc_lines = []
        for i, p in enumerate(processed, 1):
            name = (p.get("product_name", "") or "")[:50]
            price = p.get("price", 0)
            unit_price = p.get("unit_price") or p.get("unit_price_per_100")
            unit_type = p.get("unit_type", "-")
            unit_value = p.get("unit_value")
            market = p.get("market_name", "")
            unit_str = f"{unit_value} {unit_type}" if unit_value and unit_type != "-" else "-"
            up_str = f"{unit_price:.2f} TL" if unit_price is not None else "-"
            proc_lines.append(
                f"  {i:2}. {name:<50} | {price:>7.2f} TL | Birim: {unit_str} | Unit price: {up_str} | {market}"
            )
        if not proc_lines:
            proc_lines.append("  (İşlenecek ürün yok)")
        _print_section("İşlenmiş Birim Fiyatlar", proc_lines)

        # 3. AIService.generate_shopping_advice
        logger.info("AI tavsiyesi alınıyor...")
        advice = await ai_service.generate_shopping_advice(
            user_query=QUERY,
            processed_data=processed,
        )

        # AI Tavsiyesi
        _print_section("AI Tavsiyesi", advice)

        logger.info("Full-chain test tamamlandı.")

    except Exception as e:
        logger.exception("Full-chain test hatası: %s", e)
        raise
    finally:
        if bot_manager:
            await bot_manager.close()
        await close_redis()


if __name__ == "__main__":
    asyncio.run(main())
