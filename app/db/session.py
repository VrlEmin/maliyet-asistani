"""
Veritabanı bağlantısı – asyncpg / sessionmaker & Redis.
"""

import logging
from typing import AsyncGenerator

from redis.asyncio import ConnectionPool, Redis
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.db.base_class import Base

logger = logging.getLogger(__name__)

# ── SQLAlchemy Async Engine ───────────────────────────────────────────────────
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
)

async_session = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends ile kullanılacak async DB session generator."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Redis ────────────────────────────────────────────────────────────────────
_redis_pool: ConnectionPool | None = None
_redis_client: Redis | None = None


async def init_redis() -> Redis:
    """Uygulama başlangıcında Redis bağlantı havuzunu oluşturur."""
    global _redis_pool, _redis_client
    try:
        logger.info("Redis bağlantısı başlatılıyor...")
        _redis_pool = ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=50,
            decode_responses=True,
        )
        _redis_client = Redis(connection_pool=_redis_pool)
        await _redis_client.ping()
        logger.info("Redis bağlantısı hazır.")
        return _redis_client
    except Exception as e:
        logger.exception("Redis bağlantı hatası: %s", e)
        raise


async def get_redis() -> Redis:
    """Mevcut Redis client'ını döndürür."""
    if _redis_client is None:
        return await init_redis()
    return _redis_client


async def close_redis() -> None:
    """Uygulama kapanışında Redis bağlantılarını temizler."""
    global _redis_client, _redis_pool
    if _redis_client:
        await _redis_client.close()
        _redis_client = None
    if _redis_pool:
        await _redis_pool.disconnect()
        _redis_pool = None
