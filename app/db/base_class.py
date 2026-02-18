"""SQLAlchemy Declarative Base."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Tüm ORM modellerinin türediği base sınıf."""
    pass
