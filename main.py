"""
Maliyet Asistanı – FastAPI Ana Uygulama

Market fiyat karşılaştırma ve Gemini AI destekli finansal koçluk API'si.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from models.database import Base, engine, get_redis, init_redis, close_redis
from models.schemas import (
    ProductSearchRequest,
    PriceComparisonResponse,
    PriceItem,
    StandardPriceItem,
    AraRequest,
    AraResponse,
    AIAnalysisRequest,
    AIAnalysisResponse,
    NearbyMarketsRequest,
    ShoppingAdviceRequest,
    ShoppingAdviceResponse,
)
from services.bot_manager import BotManager
from services.maps_service import MapsService
from services.ai_service import AIService
from services.filter_service import FilterService
from services.data_processor import DataProcessor

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ── Global Servis Instance'ları ──────────────────────────────────────────────
bot_manager: BotManager | None = None
maps_service: MapsService | None = None
ai_service: AIService | None = None
filter_service: FilterService | None = None
data_processor: DataProcessor | None = None


# ── Lifecycle ────────────────────────────────────────────────────────────────
async def _wait_for_db(max_attempts: int = 30) -> None:
    """PostgreSQL hazır olana kadar bekler (Restarting döngüsünü önler)."""
    import asyncio
    for attempt in range(1, max_attempts + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("PostgreSQL bağlantısı ve tablolar hazır.")
            return
        except Exception as e:
            logger.warning("PostgreSQL bekleniyor (deneme %d/%d): %s", attempt, max_attempts, e)
            if attempt == max_attempts:
                raise
            await asyncio.sleep(2)


async def _wait_for_redis(max_attempts: int = 30) -> "Redis":
    """Redis hazır olana kadar bekler."""
    import asyncio
    from redis.asyncio import Redis
    for attempt in range(1, max_attempts + 1):
        try:
            client = await init_redis()
            await client.ping()
            logger.info("Redis bağlantısı hazır.")
            return client
        except Exception as e:
            logger.warning("Redis bekleniyor (deneme %d/%d): %s", attempt, max_attempts, e)
            if attempt == max_attempts:
                raise
            await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Uygulama başlangıç ve kapanış işlemleri."""
    global bot_manager, maps_service, ai_service, filter_service, data_processor

    logger.info("Maliyet Asistanı başlatılıyor...")

    try:
        await _wait_for_db()
        redis_client = await _wait_for_redis()
    except Exception as e:
        logger.exception("Başlangıç hatası (DB/Redis): %s", e)
        raise

    # Servisleri başlat
    bot_manager = BotManager(redis_client)
    maps_service = MapsService()
    ai_service = AIService()
    filter_service = FilterService(ai_service)
    data_processor = DataProcessor()
    logger.info(
        "Servisler hazır – Aktif botlar: %s",
        ", ".join(bot_manager.scraper_names),
    )

    yield  # ← Uygulama çalışıyor

    # Kapanış
    logger.info("Maliyet Asistanı kapatılıyor...")
    if bot_manager:
        await bot_manager.close()
    if maps_service:
        await maps_service.close()
    await close_redis()
    await engine.dispose()
    logger.info("Tüm bağlantılar temiz şekilde kapatıldı.")


# ── FastAPI Uygulaması ───────────────────────────────────────────────────────
app = FastAPI(
    title="Maliyet Asistanı API",
    description=(
        "Türkiye'deki büyük market zincirlerinin (Migros, BİM, A101, Tarım Kredi) "
        "fiyatlarını karşılaştıran ve Gemini AI ile finansal koçluk sunan API."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Prodüksiyonda sınırlandırılmalı
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════════════════
#  ENDPOINT'LER
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/health", tags=["Sistem"])
async def health_check() -> dict[str, str]:
    """Sağlık kontrolü."""
    return {"status": "ok", "service": "Maliyet Asistanı API"}


# ── Ürün Arama & Fiyat Karşılaştırma ────────────────────────────────────────


@app.post(
    "/api/v1/search",
    response_model=PriceComparisonResponse,
    tags=["Fiyat Karşılaştırma"],
    summary="Tüm marketlerde ürün ara ve fiyat karşılaştır",
)
async def search_products(request: ProductSearchRequest) -> PriceComparisonResponse:
    """
    Kullanıcının aradığı ürünü tüm market botlarında paralel olarak arar,
    FilterService ile temizler ve fiyat karşılaştırması döndürür.
    """
    # Servisler initialize edildiyse (None değilse), çalışmasına izin ver
    if bot_manager is None or filter_service is None or data_processor is None:
        missing = []
        if bot_manager is None:
            missing.append("bot_manager")
        if filter_service is None:
            missing.append("filter_service")
        if data_processor is None:
            missing.append("data_processor")
        logger.error("Hazır olmayan servisler: %s", missing)
        raise HTTPException(status_code=503, detail=f"Servis henüz hazır değil: {', '.join(missing)}")

    # BotManager'dan tüm marketlerden paralel arama (concurrency, deduplication, sorting dahil)
    raw = await bot_manager.search_all_markets(request.query)

    # ── DataProcessor pipeline: Normalization → Unit Price → Filtering → Smart Ranking ───────────
    processed_products = data_processor.process(raw["results"])

    # FilterService pipeline uygula
    filtered = await filter_service.filter_and_rank(
        query=request.query,
        products=processed_products,
    )

    # Ham sonuçları Pydantic modeline dönüştür
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


# ── /ara: Tüm marketlerden arama + Gemini yorumu ────────────────────────────

@app.post(
    "/ara",
    response_model=AraResponse,
    tags=["Fiyat Karşılaştırma"],
    summary="Tüm marketlerde ara, birleşik veri + AI yorumu dön",
)
@app.post(
    "/api/v1/ara",
    response_model=AraResponse,
    tags=["Fiyat Karşılaştırma"],
    summary="Tüm marketlerde ara (v1), birleşik veri + AI yorumu dön",
)
async def ara(request: AraRequest) -> AraResponse:
    """
    Migros, A101, BİM, ŞOK ve Tarım Kredi'den paralel arama yapar;
    sonuçları FilterService pipeline'ından geçirir (blacklist, dinamik kelime,
    dedup, 1 kg normalize fiyat, AI re-ranking) ve Gemini ile özetler.
    """
    # Servisler initialize edildiyse (None değilse), çalışmasına izin ver
    missing = []
    if bot_manager is None:
        missing.append("bot_manager")
    if ai_service is None:
        missing.append("ai_service")
    if filter_service is None:
        missing.append("filter_service")
    if data_processor is None:
        missing.append("data_processor")
    if missing:
        logger.error("Hazır olmayan servisler: %s", missing)
        raise HTTPException(status_code=503, detail=f"Servis henüz hazır değil: {', '.join(missing)}")

    # BotManager'dan tüm marketlerden paralel arama (concurrency, deduplication, sorting dahil)
    raw = await bot_manager.search_all_markets(request.query)

    # ── DataProcessor pipeline: Normalization → Unit Price → Filtering → Smart Ranking ───────────
    processed_products = data_processor.process(raw["results"])

    # ── FilterService pipeline: filtrele, normalize et, sırala ───────────
    filtered_products = await filter_service.filter_and_rank(
        query=request.query,
        products=processed_products,
    )

    # Cheapest / most expensive hesapla (filtrelenmiş listeden)
    cheapest_dict = filtered_products[0] if filtered_products else None
    most_expensive_dict = filtered_products[-1] if filtered_products else None
    potential_saving = 0.0
    if cheapest_dict and most_expensive_dict:
        potential_saving = round(
            most_expensive_dict["price"] - cheapest_dict["price"], 2,
        )

    # ── Konum bazlı en yakın market ──────────────────────────────────────
    nearest_market: dict[str, Any] | None = None
    if request.latitude is not None and request.longitude is not None and maps_service:
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

    # ── Gemini AI özeti ──────────────────────────────────────────────────
    ai_summary = await ai_service.compare_and_summarize(
        query=request.query,
        results=filtered_products,
        cheapest=cheapest_dict,
        most_expensive=most_expensive_dict,
        potential_saving=potential_saving,
        nearest_market=nearest_market,
    )

    # ── Pydantic modeline dönüştür ───────────────────────────────────────
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

    results = [_to_item(r) for r in filtered_products]

    return AraResponse(
        query=request.query,
        results=results,
        cheapest=_to_item(cheapest_dict) if cheapest_dict else None,
        most_expensive=_to_item(most_expensive_dict) if most_expensive_dict else None,
        potential_saving=potential_saving,
        ai_summary=ai_summary,
    )


# ── Fiyat Geçmişi ───────────────────────────────────────────────────────────


@app.get(
    "/api/v1/history/{product_id}",
    tags=["Fiyat Karşılaştırma"],
    summary="Ürün fiyat geçmişi",
)
async def get_price_history(product_id: int) -> dict[str, Any]:
    """
    Belirli bir ürünün tüm marketlerdeki fiyat geçmişini döndürür.

    Not: Tam implementasyon PostgreSQL sorgusu ile yapılacak.
    Şu an botlardan anlık fiyat çeker.
    """
    if bot_manager is None:
        logger.error("bot_manager servisi hazır değil")
        raise HTTPException(status_code=503, detail="Servis henüz hazır değil: bot_manager")

    prices = await bot_manager.get_price_from_all(str(product_id))
    return {
        "product_id": product_id,
        "current_prices": prices,
        "history": [],  # TODO: PostgreSQL'den geçmiş fiyatlar çekilecek
    }


# ── AI Analiz ────────────────────────────────────────────────────────────────


@app.post(
    "/api/v1/analyze",
    response_model=AIAnalysisResponse,
    tags=["AI Analiz"],
    summary="AI tasarruf analizi ve yatırım koçluğu",
)
async def analyze_savings(request: AIAnalysisRequest) -> AIAnalysisResponse:
    """
    Fiyat karşılaştırma verisini Gemini 1.5 Flash ile analiz eder.
    Tasarruf raporu ve yatırım önerileri döndürür.
    """
    if ai_service is None:
        logger.error("ai_service servisi hazır değil")
        raise HTTPException(status_code=503, detail="AI servisi henüz hazır değil")

    comparison_dict = request.comparison.model_dump()

    # Paralel: tasarruf analizi + yatırım koçluğu
    import asyncio

    saving_task = ai_service.analyze_savings(comparison_dict)
    investment_task = ai_service.investment_coaching(
        saved_amount=request.comparison.potential_saving,
        monthly_budget=request.monthly_grocery_budget,
    )

    saving_text, investment_text = await asyncio.gather(
        saving_task, investment_task
    )

    return AIAnalysisResponse(
        saving_analysis=saving_text,
        investment_advice=investment_text,
        estimated_monthly_saving=round(
            request.comparison.potential_saving * 8, 2
        ),  # Ayda ~8 alışveriş
    )


@app.post(
    "/api/v1/shopping-advice",
    response_model=ShoppingAdviceResponse,
    tags=["AI Analiz"],
    summary="Akıllı alışveriş tavsiyesi",
)
async def get_shopping_advice(request: ShoppingAdviceRequest) -> ShoppingAdviceResponse:
    """
    Sadece query alarak marketleri tarar, verileri işler ve AI tavsiyesi üretir.

    Akış: BotManager → DataProcessor → FilterService → AIService.
    Ürünleri tablo formatında gösterir ve birim fiyatlar arasında
    önemli farklar varsa kullanıcıyı uyarır.
    """
    # Servisler initialize edildiyse (None değilse), çalışmasına izin ver
    missing = []
    if bot_manager is None:
        missing.append("bot_manager")
    if data_processor is None:
        missing.append("data_processor")
    if filter_service is None:
        missing.append("filter_service")
    if ai_service is None:
        missing.append("ai_service")
    if missing:
        logger.error("Hazır olmayan servisler: %s", missing)
        raise HTTPException(status_code=503, detail=f"Servis henüz hazır değil: {', '.join(missing)}")

    # 1. BotManager ile market taraması
    raw = await bot_manager.search_all_markets(request.query)

    # 2. DataProcessor ile birim fiyat işleme
    processed_products = data_processor.process(raw["results"])

    # 3. FilterService ile filtreleme ve sıralama
    filtered_products = await filter_service.filter_and_rank(
        query=request.query,
        products=processed_products,
    )

    # 4. AIService ile tavsiye üretimi
    advice = await ai_service.generate_shopping_advice(
        user_query=request.query,
        processed_data=filtered_products,
    )

    # 5. Ürün listesini StandardPriceItem'a dönüştür
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

    results = [_to_item(r) for r in filtered_products]
    return ShoppingAdviceResponse(query=request.query, advice=advice, results=results)


# ── Yakın Marketler ─────────────────────────────────────────────────────────


@app.get(
    "/api/v1/markets/nearby",
    tags=["Konum"],
    summary="Yakındaki marketleri listele",
)
async def find_nearby_markets(
    latitude: float = Query(..., description="Kullanıcı enlemi"),
    longitude: float = Query(..., description="Kullanıcı boylamı"),
    radius_km: float = Query(default=5.0, ge=0.5, le=50.0, description="Arama yarıçapı (km)"),
) -> dict[str, Any]:
    """
    Kullanıcının konumuna göre en yakın Migros, BİM, A101, ŞOK ve
    Tarım Kredi marketlerini Google Places API ile bulur.
    """
    if maps_service is None:
        logger.error("maps_service servisi hazır değil")
        raise HTTPException(status_code=503, detail="Harita servisi henüz hazır değil")

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
