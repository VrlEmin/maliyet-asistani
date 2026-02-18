"""
Maliyet Asistanı – FastAPI ana giriş noktası.

Market fiyat karşılaştırma ve Gemini AI destekli finansal koçluk API'si.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.api.v1.endpoints import ara, health
from app.db import Base, close_redis, engine, init_redis
from services.bot_manager import BotManager
from services.maps_service import MapsService
from services.ai_service import AIService
from services.filter_service import FilterService
from services.data_processor import DataProcessor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def _wait_for_db(max_attempts: int = 30) -> None:
    """PostgreSQL hazır olana kadar bekler."""
    for attempt in range(1, max_attempts + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("PostgreSQL bağlantısı ve tablolar hazır.")
            return
        except Exception as e:
            logger.warning("PostgreSQL bekleniyor (deneme %d/%d): %s", attempt, max_attempts, e)
            if attempt == max_attempts:
                raise
            await asyncio.sleep(2)


async def _wait_for_redis(max_attempts: int = 30):
    """Redis hazır olana kadar bekler."""
    for attempt in range(1, max_attempts + 1):
        try:
            client = await init_redis()
            await client.ping()
            logger.info("Redis bağlantısı hazır.")
            return client
        except Exception as e:
            logger.warning("Redis bekleniyor (deneme %d/%d): %s", attempt, max_attempts, e)
            if attempt == max_attempts:
                raise
            await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Uygulama başlangıç ve kapanış işlemleri."""
    logger.info("Maliyet Asistanı başlatılıyor...")
    try:
        await _wait_for_db()
        redis_client = await _wait_for_redis()
    except Exception as e:
        logger.exception("Başlangıç hatası (DB/Redis): %s", e)
        raise

    bot_manager = BotManager(redis_client)
    maps_service = MapsService()
    ai_service = AIService()
    filter_service = FilterService(ai_service)
    data_processor = DataProcessor()

    app.state.bot_manager = bot_manager
    app.state.maps_service = maps_service
    app.state.ai_service = ai_service
    app.state.filter_service = filter_service
    app.state.data_processor = data_processor

    logger.info("Servisler hazır – Aktif botlar: %s", ", ".join(bot_manager.scraper_names))

    yield

    logger.info("Maliyet Asistanı kapatılıyor...")
    await bot_manager.close()
    await maps_service.close()
    await close_redis()
    await engine.dispose()
    logger.info("Tüm bağlantılar temiz şekilde kapatıldı.")


app = FastAPI(
    title="Maliyet Asistanı API",
    description=(
        "Türkiye'deki büyük market zincirlerinin (Migros, BİM, A101, Tarım Kredi) "
        "fiyatlarını karşılaştıran ve Gemini AI ile finansal koçluk sunan API."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Root seviye route'lar (geriye uyumluluk)
app.include_router(health.router)
app.include_router(ara.router, prefix="/ara")

# v1 API
app.include_router(api_router, prefix="/api/v1")
