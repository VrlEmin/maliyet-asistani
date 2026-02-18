"""
Uygulama ayarları – Pydantic Settings ile env yönetimi.
"""

import os
from urllib.parse import urlparse, urlunparse

from pydantic import ConfigDict, model_validator
from pydantic_settings import BaseSettings


def _is_running_in_docker() -> bool:
    """
    Docker konteyneri içinde çalışıp çalışmadığını tespit eder.
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
    """Yerelde çalışırken redis/postgres host'larını localhost yapar."""
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


class Settings(BaseSettings):
    """Ortam değişkenleri ile yüklenen uygulama ayarları."""

    model_config = ConfigDict(
        env_file=".env",
        extra="ignore",
    )

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
