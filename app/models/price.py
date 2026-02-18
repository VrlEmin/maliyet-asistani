from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class Price(Base):
    """Belirli bir marketteki ürün fiyat kaydı."""

    __tablename__ = "prices"
    __table_args__ = (
        Index("ix_prices_product_market", "product_id", "market_id"),
        Index("ix_prices_scraped_at", "scraped_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    market_id = Column(Integer, ForeignKey("markets.id"), nullable=False)
    price = Column(Float, nullable=False)
    currency = Column(String(10), default="TRY")
    scraped_at = Column(DateTime, server_default=func.now(), nullable=False)

    product = relationship("Product", back_populates="prices")
    market = relationship("Market", back_populates="prices")

    def __repr__(self) -> str:
        return f"<Price(product={self.product_id}, market={self.market_id}, price={self.price})>"
