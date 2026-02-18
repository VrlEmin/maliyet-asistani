from sqlalchemy import Column, Float, Integer, String
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class Market(Base):
    """Market zinciri bilgisi (Migros, BİM, A101, ŞOK, Tarım Kredi)."""

    __tablename__ = "markets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, index=True)
    branch_name = Column(String(200), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    address = Column(String(500), nullable=True)

    prices = relationship("Price", back_populates="market", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Market(id={self.id}, name='{self.name}')>"
