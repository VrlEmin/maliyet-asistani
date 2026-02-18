import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_ai_service, get_data_processor, get_filter_service, get_maps_service_optional, get_bot_manager
from app.schemas import AraRequest, AraResponse, StandardPriceItem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ara", tags=["Fiyat Karşılaştırma"])


def _to_item(r: dict) -> StandardPriceItem:
    return StandardPriceItem(
        product_name=r["product_name"],
        price=r["price"],
        gramaj=r.get("gramaj"),
        unit_price=r.get("unit_price") or r.get("unit_price_per_100"),
        unit_price_per_100=r.get("unit_price_per_100") or r.get("unit_price"),
        unit_type=r.get("unit_type"),
        unit_value=r.get("unit_value"),
        unit_price_per_kg=r.get("unit_price_per_kg"),
        normalized_price_per_kg=r.get("normalized_price_per_kg"),
        market_name=r["market_name"],
        currency=r.get("currency", "TRY"),
        image_url=r.get("image_url"),
    )


@router.post("", response_model=AraResponse, summary="Tüm marketlerde ara + AI yorumu")
async def ara(
    request: AraRequest,
    bot_manager=Depends(get_bot_manager),
    ai_service=Depends(get_ai_service),
    filter_service=Depends(get_filter_service),
    data_processor=Depends(get_data_processor),
    maps_service=Depends(get_maps_service_optional),
) -> AraResponse:
    """
    Tüm marketlerde ara; birleşik veri + Gemini yorumu dön.
    """
    raw = await bot_manager.search_all_markets(request.query)
    processed_products = data_processor.process(raw["results"])
    filtered_products = await filter_service.filter_and_rank(
        query=request.query,
        products=processed_products,
    )
    cheapest_dict = filtered_products[0] if filtered_products else None
    most_expensive_dict = filtered_products[-1] if filtered_products else None
    potential_saving = 0.0
    if cheapest_dict and most_expensive_dict:
        potential_saving = round(most_expensive_dict["price"] - cheapest_dict["price"], 2)

    nearest_market: dict[str, Any] | None = None
    if request.latitude is not None and request.longitude is not None and maps_service is not None:
        try:
            nearby = await maps_service.find_nearby_markets(
                latitude=request.latitude,
                longitude=request.longitude,
                radius_km=request.radius_km,
            )
            if nearby:
                nearest_market = nearby[0]
        except Exception as e:
            logger.warning("Yakın market bulunamadı: %s", e)

    ai_summary = await ai_service.compare_and_summarize(
        query=request.query,
        results=filtered_products,
        cheapest=cheapest_dict,
        most_expensive=most_expensive_dict,
        potential_saving=potential_saving,
        nearest_market=nearest_market,
    )

    results = [_to_item(r) for r in filtered_products]
    return AraResponse(
        query=request.query,
        results=results,
        cheapest=_to_item(cheapest_dict) if cheapest_dict else None,
        most_expensive=_to_item(most_expensive_dict) if most_expensive_dict else None,
        potential_saving=potential_saving,
        ai_summary=ai_summary,
    )
