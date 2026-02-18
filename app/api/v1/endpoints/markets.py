from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_maps_service

router = APIRouter(prefix="/markets", tags=["Konum"])


@router.get("/nearby")
async def find_nearby_markets(
    latitude: float = Query(..., description="Kullanıcı enlemi"),
    longitude: float = Query(..., description="Kullanıcı boylamı"),
    radius_km: float = Query(default=5.0, ge=0.5, le=50.0, description="Arama yarıçapı (km)"),
    maps_service=Depends(get_maps_service),
) -> dict[str, Any]:
    """Konuma göre en yakın marketleri Google Places API ile listeler."""
    markets = await maps_service.find_nearby_markets(
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
    )
    return {
        "latitude": latitude,
        "longitude": longitude,
        "radius_km": radius_km,
        "total_found": len(markets),
        "markets": markets,
    }
