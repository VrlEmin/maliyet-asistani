import asyncio
import logging

from fastapi import APIRouter, Depends

from app.api.deps import get_ai_service, get_bot_manager, get_data_processor, get_filter_service
from app.schemas import (
    AIAnalysisRequest,
    AIAnalysisResponse,
    ShoppingAdviceRequest,
    ShoppingAdviceResponse,
    StandardPriceItem,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AI Analiz"])


@router.post("/analyze", response_model=AIAnalysisResponse)
async def analyze_savings(
    request: AIAnalysisRequest,
    ai_service=Depends(get_ai_service),
) -> AIAnalysisResponse:
    """Fiyat karşılaştırmasını Gemini ile analiz eder; tasarruf ve yatırım önerisi."""
    comparison_dict = request.comparison.model_dump()
    saving_task = ai_service.analyze_savings(comparison_dict)
    investment_task = ai_service.investment_coaching(
        saved_amount=request.comparison.potential_saving,
        monthly_budget=request.monthly_grocery_budget,
    )
    saving_text, investment_text = await asyncio.gather(saving_task, investment_task)
    return AIAnalysisResponse(
        saving_analysis=saving_text,
        investment_advice=investment_text,
        estimated_monthly_saving=round(request.comparison.potential_saving * 8, 2),
    )


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


@router.post("/shopping-advice", response_model=ShoppingAdviceResponse)
async def get_shopping_advice(
    request: ShoppingAdviceRequest,
    bot_manager=Depends(get_bot_manager),
    data_processor=Depends(get_data_processor),
    filter_service=Depends(get_filter_service),
    ai_service=Depends(get_ai_service),
) -> ShoppingAdviceResponse:
    """Sadece sorgu ile market tarar ve AI alışveriş tavsiyesi üretir."""
    raw = await bot_manager.search_all_markets(request.query)
    processed_products = data_processor.process(raw["results"])
    filtered_products = await filter_service.filter_and_rank(
        query=request.query,
        products=processed_products,
    )
    advice = await ai_service.generate_shopping_advice(
        user_query=request.query,
        processed_data=filtered_products,
    )
    results = [_to_item(r) for r in filtered_products]
    return ShoppingAdviceResponse(query=request.query, advice=advice, results=results)
