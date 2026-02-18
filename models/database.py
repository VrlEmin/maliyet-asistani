"""
Async veritabanı bağlantı yönetimi – PostgreSQL (SQLAlchemy) & Redis.
"""

import os
from urllib.parse import urlparse, urlunparse

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from redis.asyncio import Redis, ConnectionPool
from pydantic_settings import BaseSettings
from pydantic import ConfigDict, model_validator


def _is_running_in_docker() -> bool:
    """Docker konteyneri içinde çalışıp çalışmadığını tespit eder.
    RUNNING_IN_DOCKER env ile manuel override mümkündür (1=docker, 0=yerel).
    """
    override = os.environ.get("RUNNING_IN_DOCKER", "").strip()
    if override == "1":
        return True
    if override == "0":
        return False
    return os.path.exists("/.dockerenv")


_DOCKER_HOSTS = ("redis", "postgres", "db")


def _resolve_url_for_local(url: str) -> str:
    """Yerelde çalışırken sadece host kısmını (URL'de @ sonrası, : öncesi) localhost yapar.
    Protokol ismine (postgresql+asyncpg vb.) dokunmaz.
    """
    if _is_running_in_docker():
        return url
    parsed = urlparse(url)
    netloc = parsed.netloc
    if "@" in netloc:
        auth, hostport = netloc.rsplit("@", 1)
        host, sep, port = hostport.partition(":")
        new_host = "localhost" if host in _DOCKER_HOSTS else host
        new_netloc = f"{auth}@{new_host}{sep}{port}"
    else:
        host, sep, port = netloc.partition(":")
        new_host = "localhost" if host in _DOCKER_HOSTS else host
        new_netloc = f"{new_host}{sep}{port}" if port else new_host
    return urlunparse((
        parsed.scheme,
        new_netloc,
        parsed.path or "",
        parsed.params or "",
        parsed.query or "",
        parsed.fragment or "",
    ))


# ── Ayarlar ───────────────────────────────────────────────────────────────────
class Settings(BaseSettings):
    model_config = ConfigDict(
        env_file=".env",
        extra="ignore",  # POSTGRES_USER, POSTGRES_PASSWORD vb. env'leri yok say
    )

    # Şablon; gerçek değer .env dosyasından okunur (USER, PASSWORD, DBNAME doldurulmalı)
    DATABASE_URL: str = "postgresql+asyncpg://user:password@postgres:5432/dbname"
    REDIS_URL: str = "redis://redis:6379/0"
    GOOGLE_MAPS_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""

    @model_validator(mode="after")
    def resolve_urls_for_local(self) -> "Settings":
        """Yerelde çalışırken redis/postgres host'larını localhost yapar."""
        if not _is_running_in_docker():
            self.REDIS_URL = _resolve_url_for_local(self.REDIS_URL)
            self.DATABASE_URL = _resolve_url_for_local(self.DATABASE_URL)
        return self


settings = Settings()

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


# ── Declarative Base ─────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Dependency: DB Session ───────────────────────────────────────────────────
async def get_db() -> AsyncSession:  # type: ignore[misc]
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


# ── Redis Bağlantı Havuzu ───────────────────────────────────────────────────
_redis_pool: ConnectionPool | None = None
_redis_client: Redis | None = None


async def init_redis() -> Redis:
    """Uygulama başlangıcında çağrılır – Redis bağlantı havuzunu oluşturur."""
    global _redis_pool, _redis_client
    
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        logger.info("Redis bağlantısı başlatılıyor...")
        logger.info("REDIS_URL: %s", settings.REDIS_URL)
        logger.info("Docker içinde mi: %s", _is_running_in_docker())
        
        _redis_pool = ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=50,
            decode_responses=True,
        )
        logger.info("Redis connection pool oluşturuldu.")
        
        _redis_client = Redis(connection_pool=_redis_pool)
        logger.info("Redis client oluşturuldu.")
        
        # Bağlantı testi
        try:
            await _redis_client.ping()
            logger.info("Redis ping başarılı - bağlantı çalışıyor.")
        except Exception as ping_err:
            logger.error("Redis ping başarısız: %s", ping_err)
            logger.exception("Redis ping hatası detayları:")
            raise
        
        return _redis_client
    except Exception as e:
        logger.error("Redis bağlantısı başlatılamadı!")
        logger.error("Hata tipi: %s", type(e).__name__)
        logger.error("Hata mesajı: %s", str(e))
        logger.exception("Redis bağlantı hatası detayları:")
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
