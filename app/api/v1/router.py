"""v1 API route birleştirici."""

from fastapi import APIRouter

from app.api.v1.endpoints import ara, analyze, health, markets, search

api_router = APIRouter()

# /api/v1 prefix main'de eklenecek
api_router.include_router(health.router)
api_router.include_router(search.router)
api_router.include_router(ara.router, prefix="/ara")
api_router.include_router(analyze.router)
api_router.include_router(markets.router)
