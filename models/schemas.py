"""
Uyumluluk katmanı – app.models ve app.schemas re-export.
Eski import'lar (services, main) çalışmaya devam eder.
"""

from app.models import Market, Price, Product
from app.schemas.common import (
    AIAnalysisRequest,
    AIAnalysisResponse,
    AraRequest,
    AraResponse,
    NearbyMarketsRequest,
    PriceComparisonResponse,
    PriceItem,
    ProductSearchRequest,
    ShoppingAdviceRequest,
    ShoppingAdviceResponse,
    StandardPriceItem,
)

__all__ = [
    "Market",
    "Product",
    "Price",
    "ProductSearchRequest",
    "PriceItem",
    "StandardPriceItem",
    "PriceComparisonResponse",
    "AIAnalysisRequest",
    "AIAnalysisResponse",
    "NearbyMarketsRequest",
    "AraRequest",
    "AraResponse",
    "ShoppingAdviceRequest",
    "ShoppingAdviceResponse",
]
