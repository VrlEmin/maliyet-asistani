"""
Redis → Gemini 2.5 Flash (normalize) → PostgreSQL senkronizasyonunu tetikler.

Kullanım (backend dizininden):
  PYTHONPATH=. python scripts/run_sync.py
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.db.session import async_session, init_redis
from app.services.data_pipeline import run_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Redis → AI normalize → PostgreSQL pipeline başlatılıyor...")
    try:
        redis_client = await init_redis()
    except Exception as e:
        logger.exception("Redis bağlantısı kurulamadı: %s", e)
        sys.exit(1)

    async with async_session() as session:
        try:
            stats = await run_pipeline(redis_client, session)
            await session.commit()
            logger.info(
                "Pipeline tamamlandı: keys_read=%s, records_processed=%s, prices_inserted=%s, errors=%s",
                stats.get("keys_read", 0),
                stats.get("records_processed", 0),
                stats.get("prices_inserted", 0),
                stats.get("errors", 0),
            )
        except Exception as e:
            await session.rollback()
            logger.exception("Pipeline hatası: %s", e)
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
