import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_bot_manager, get_data_processor, get_filter_service
from app.schemas import PriceComparisonResponse, PriceItem, ProductSearchRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Fiyat Karşılaştırma"])


@router.post("/search", response_model=PriceComparisonResponse)
async def search_products(
    request: ProductSearchRequest,
    bot_manager=Depends(get_bot_manager),
    filter_service=Depends(get_filter_service),
    data_processor=Depends(get_data_processor),
) -> PriceComparisonResponse:
    """
    Tüm marketlerde ürün ara ve fiyat karşılaştır.
    """
    raw = await bot_manager.search_all_markets(request.query)
    processed_products = data_processor.process(raw["results"])
    filtered = await filter_service.filter_and_rank(
        query=request.query,
        products=processed_products,
    )
    results = [
        PriceItem(
            market_name=r["market_name"],
            product_name=r["product_name"],
            price=r["price"],
            currency=r.get("currency", "TRY"),
            image_url=r.get("image_url"),
        )
        for r in filtered
    ]
    cheapest = results[0] if results else None
    most_expensive = results[-1] if results else None
    potential_saving = 0.0
    if cheapest and most_expensive:
        potential_saving = round(most_expensive.price - cheapest.price, 2)
    return PriceComparisonResponse(
        query=request.query,
        results=results,
        cheapest=cheapest,
        most_expensive=most_expensive,
        potential_saving=potential_saving,
    )


@router.get("/history/{product_id}", summary="Ürün fiyat geçmişi")
async def get_price_history(
    product_id: int,
    bot_manager=Depends(get_bot_manager),
) -> dict[str, Any]:
    """Ürün fiyat geçmişi (anlık fiyatlar; geçmiş için DB kullanılacak)."""
    prices = await bot_manager.get_price_from_all(str(product_id))
    return {
        "product_id": product_id,
        "current_prices": prices,
        "history": [],
    }
