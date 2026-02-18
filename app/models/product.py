from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class Product(Base):
    """Ürün bilgisi."""

    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(300), nullable=False, index=True)
    category = Column(String(100), nullable=True)
    barcode = Column(String(50), nullable=True, unique=True)
    image_url = Column(String(500), nullable=True)

    prices = relationship("Price", back_populates="product", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Product(id={self.id}, name='{self.name}')>"
