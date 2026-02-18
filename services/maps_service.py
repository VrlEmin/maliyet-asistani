"""
MapsService – Google Places API ile koordinat bazlı market tespiti.

Kullanıcının konumuna en yakın Migros, BİM, A101, ŞOK ve Tarım Kredi
marketlerini bulur.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from models.database import settings

logger = logging.getLogger(__name__)

# Desteklenen market zincirleri (Google Places'ta aranacak isimler)
SUPPORTED_CHAINS: list[str] = [
    "Migros",
    "BİM",
    "A101",
    "ŞOK",
    "Tarım Kredi",
]

PLACES_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"


class MapsService:
    """Google Places API entegrasyonu."""

    def __init__(self) -> None:
        self.api_key = settings.GOOGLE_MAPS_API_KEY
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ── Yakın Marketleri Bul ─────────────────────────────────────────────────

    async def find_nearby_markets(
        self,
        latitude: float,
        longitude: float,
        radius_km: float = 5.0,
    ) -> list[dict[str, Any]]:
        """
        Verilen koordinatlar etrafındaki desteklenen market zincirlerini bulur.

        Returns:
            [
                {
                    "name": "Migros",
                    "branch_name": "Migros Jet Çankaya",
                    "latitude": 39.92,
                    "longitude": 32.85,
                    "address": "...",
                    "distance_km": 0.3,
                },
                ...
            ]
        """
        radius_meters = int(radius_km * 1000)
        all_markets: list[dict[str, Any]] = []

        client = await self._get_client()

        for chain in SUPPORTED_CHAINS:
            try:
                response = await client.get(
                    PLACES_NEARBY_URL,
                    params={
                        "location": f"{latitude},{longitude}",
                        "radius": radius_meters,
                        "keyword": chain,
                        "type": "supermarket",
                        "key": self.api_key,
                        "language": "tr",
                    },
                )
                response.raise_for_status()
                data = response.json()

                for place in data.get("results", []):
                    loc = place.get("geometry", {}).get("location", {})
                    place_lat = loc.get("lat", 0)
                    place_lng = loc.get("lng", 0)

                    distance = self._haversine(
                        latitude, longitude, place_lat, place_lng
                    )

                    all_markets.append(
                        {
                            "name": chain,
                            "branch_name": place.get("name", chain),
                            "latitude": place_lat,
                            "longitude": place_lng,
                            "address": place.get("vicinity", ""),
                            "distance_km": round(distance, 2),
                            "place_id": place.get("place_id"),
                            "rating": place.get("rating"),
                        }
                    )

            except Exception as exc:
                logger.error(
                    "[MapsService] '%s' market araması hatası: %s", chain, exc
                )
                continue

        # Mesafeye göre sırala
        all_markets.sort(key=lambda m: m["distance_km"])
        return all_markets

    # ── Haversine Mesafe Hesabı ──────────────────────────────────────────────

    @staticmethod
    def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """İki koordinat arasındaki mesafeyi km cinsinden hesaplar."""
        import math

        R = 6371.0  # Dünya yarıçapı (km)
        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        a = (
            math.sin(d_lat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(d_lon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c
