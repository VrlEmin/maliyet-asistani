"""Pydantic şemaları – Request / Response modelleri."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ProductSearchRequest(BaseModel):
    """Ürün arama isteği."""
    query: str = Field(..., min_length=2, max_length=200, description="Aranacak ürün adı")
    latitude: Optional[float] = Field(None, description="Kullanıcı enlemi")
    longitude: Optional[float] = Field(None, description="Kullanıcı boylamı")


class PriceItem(BaseModel):
    """Tek bir market-fiyat çifti."""
    market_name: str
    product_name: str
    price: float
    currency: str = "TRY"
    image_url: Optional[str] = None
    scraped_at: Optional[datetime] = None


class StandardPriceItem(BaseModel):
    """Standart ürün-fiyat-gramaj şablonu (tüm marketler için ortak)."""
    product_name: str
    price: float
    gramaj: Optional[float] = Field(None, description="Gram cinsinden (örn: 500, 1000)")
    unit_price: Optional[float] = Field(None, description="100 birim başına fiyat (TL)")
    unit_price_per_100: Optional[float] = Field(None, description="100 birim başına fiyat (TL) - alias")
    unit_type: Optional[str] = Field(None, description="Birim tipi (kg, g, lt, ml, adet)")
    unit_value: Optional[float] = Field(None, description="Birim değeri (örn: 1.0, 500.0)")
    unit_price_per_kg: Optional[float] = Field(None, description="1 kg birim fiyatı (TL)")
    normalized_price_per_kg: Optional[float] = Field(None, description="1 kg normalize fiyat (TL)")
    market_name: str
    currency: str = "TRY"
    image_url: Optional[str] = None


class PriceComparisonResponse(BaseModel):
    """Ürün fiyat karşılaştırma yanıtı."""
    query: str
    results: list[PriceItem] = []
    cheapest: Optional[PriceItem] = None
    most_expensive: Optional[PriceItem] = None
    potential_saving: float = 0.0


class AIAnalysisRequest(BaseModel):
    """AI analiz isteği."""
    comparison: PriceComparisonResponse
    monthly_grocery_budget: Optional[float] = Field(None, description="Aylık market bütçesi (TL)")


class AIAnalysisResponse(BaseModel):
    """AI tasarruf analizi ve yatırım koçluğu yanıtı."""
    saving_analysis: str = Field(..., description="Tasarruf analizi metni")
    investment_advice: str = Field(..., description="Yatırım önerisi metni")
    estimated_monthly_saving: Optional[float] = None


class NearbyMarketsRequest(BaseModel):
    """Yakın market arama isteği."""
    latitude: float
    longitude: float
    radius_km: float = Field(default=5.0, ge=0.5, le=50.0)


class AraRequest(BaseModel):
    """/ara endpoint isteği – tüm marketlerden arama + AI yorumu."""
    query: str = Field(..., min_length=2, max_length=200)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_km: float = Field(default=5.0, ge=0.5, le=50.0)


class AraResponse(BaseModel):
    """/ara endpoint yanıtı – birleşik veri + Gemini yorumu."""
    query: str
    results: list[StandardPriceItem] = []
    cheapest: Optional[StandardPriceItem] = None
    most_expensive: Optional[StandardPriceItem] = None
    potential_saving: float = 0.0
    ai_summary: str = Field(..., description="Gemini: en ucuz market + konuma en yakın + fark")


class ShoppingAdviceRequest(BaseModel):
    """Alışveriş tavsiyesi isteği (sadece sorgu)."""
    query: str = Field(..., min_length=2, max_length=200)


class ShoppingAdviceResponse(BaseModel):
    """Alışveriş tavsiyesi yanıtı."""
    query: str
    advice: str = Field(..., description="AI tarafından üretilen tavsiye metni")
    results: list[StandardPriceItem] = Field(default_factory=list, description="Karşılaştırmalı ürün listesi")
