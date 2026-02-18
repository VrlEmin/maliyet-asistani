"""
BİM Bot Test Scripti

BİM scraper'ını direkt test eder.
"""

import asyncio
import json
import logging
import sys

from models.database import init_redis, close_redis
from scrapers.bim_bot import BimScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def test_bim_bot(query: str = "pirinç"):
    """BİM botunu test eder."""
    logger.info("=" * 60)
    logger.info("BİM Bot Test Başlıyor")
    logger.info("=" * 60)
    
    # Redis bağlantısı
    try:
        redis_client = await init_redis()
        logger.info("✓ Redis bağlantısı başarılı")
    except Exception as e:
        logger.error("✗ Redis bağlantı hatası: %s", e)
        return
    
    # BİM scraper oluştur
    scraper = BimScraper(redis_client)
    logger.info("✓ BimScraper instance oluşturuldu")
    logger.info("")
    
    # Test sorgusu
    logger.info(f"🔍 Test Sorgusu: '{query}'")
    logger.info("")
    
    try:
        # Cache'i temizle (test için)
        cache_key = f"scraper:BIM:search:{query.lower().strip()}"
        await redis_client.delete(cache_key)
        logger.info("✓ Cache temizlendi (fresh test)")
        logger.info("")
        
        # Arama yap
        logger.info("📡 BİM.com.tr'den ürün aranıyor...")
        results = await scraper.search_product(query)
        
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"SONUÇLAR ({len(results)} ürün bulundu)")
        logger.info("=" * 60)
        
        if not results:
            logger.warning("⚠️  Hiç ürün bulunamadı!")
            logger.info("")
            logger.info("Olası nedenler:")
            logger.info("  1. BİM aktüel kataloğunda bu ürün yok")
            logger.info("  2. HTML yapısı değişmiş olabilir")
            logger.info("  3. okatalog.com fallback deneniyor...")
        else:
            for i, product in enumerate(results, 1):
                logger.info("")
                logger.info(f"Ürün #{i}:")
                logger.info(f"  📦 İsim: {product.get('product_name', 'N/A')}")
                logger.info(f"  💰 Fiyat: {product.get('price', 0):.2f} {product.get('currency', 'TRY')}")
                gramaj = product.get('gramaj')
                if gramaj:
                    logger.info(f"  ⚖️  Gramaj: {gramaj:.0f} g")
                    unit_price = product.get('unit_price_per_kg')
                    if unit_price:
                        logger.info(f"  📊 1kg Fiyat: {unit_price:.2f} TL")
                logger.info(f"  🏪 Market: {product.get('market_name', 'BIM')}")
                if product.get('image_url'):
                    logger.info(f"  🖼️  Görsel: {product['image_url']}")
        
        logger.info("")
        logger.info("=" * 60)
        logger.info("JSON Formatında Sonuçlar:")
        logger.info("=" * 60)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        
    except Exception as e:
        logger.error("✗ Hata oluştu: %s", e, exc_info=True)
    finally:
        # Temizlik
        await scraper.close()
        await close_redis()
        logger.info("")
        logger.info("✓ Bağlantılar kapatıldı")


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "pirinç"
    asyncio.run(test_bim_bot(query))
