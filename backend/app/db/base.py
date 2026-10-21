"""DeclarativeBase con convención de nombres y utilidades comunes."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase, declared_attr

# Convención de nombres: sin esto, las migraciones de constraints son un infierno.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805
        """snake_case plural automático: `BookingHold` → `booking_holds`."""
        name = cls.__name__
        out: list[str] = []
        for i, char in enumerate(name):
            if char.isupper() and i > 0:
                out.append("_")
            out.append(char.lower())
        word = "".join(out)
        if word.endswith(("s", "x", "z")):
            return f"{word}es"
        if word.endswith("y"):
            return f"{word[:-1]}ies"
        return f"{word}s"
