"""CarKey record and Stated, Silent and Unparseable field states shared by every car-name parser."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, TypeVar

from app.nav.vocab import normalize

T = TypeVar("T")


@dataclass(frozen=True)
class Stated(Generic[T]):
    """Field state for a value the source stated and the parser understood, with its raw text."""

    value: T
    raw: str


@dataclass(frozen=True)
class Silent:
    """Field state meaning the source said nothing about this field; it carries no value."""


@dataclass(frozen=True)
class Unparseable:
    """Field marker for text that was present but could not be read, keeping the raw string."""

    raw: str


FieldValue = Stated[T] | Silent | Unparseable


def stated_value(field: FieldValue):
    """Returns the field's value when it is Stated, otherwise None."""
    return field.value if isinstance(field, Stated) else None


@dataclass(frozen=True)
class Designation:
    """Brand, model and trim held as one ordered name with tokens, an alphanumeric fingerprint and raw text."""

    tokens: tuple[str, ...]
    fingerprint: str
    raw: str

    @classmethod
    def of(cls, raw: str) -> "Designation":
        """Builds a Designation from raw text using normalized tokens, fingerprint and cleaned raw string."""
        return cls(tokens=normalize.tokens(raw),
                   fingerprint=normalize.fingerprint(raw),
                   raw=normalize.clean(raw))

    def __bool__(self) -> bool:
        """Returns True when the designation has a non-empty fingerprint."""
        return bool(self.fingerprint)


@dataclass(frozen=True)
class CarKey:
    """Parsed record of one car: designation, a FieldValue per attribute from brand to VIN tag, raw text."""

    designation: Designation
    brand: FieldValue[str]
    model_head: FieldValue[str]
    trim: FieldValue[str]
    fuel: FieldValue[str]
    engine_l: FieldValue[Decimal]
    transmission: FieldValue[str]
    model_year: FieldValue[int]
    exterior: FieldValue[str] = Silent()
    interior: FieldValue[str] = Silent()
    vin_tag: FieldValue[str] = Silent()
    variant_suffix: FieldValue[str] = Silent()
    raw: str = ""

    def field(self, name: str) -> FieldValue:
        """Returns the FieldValue stored under the given attribute name."""
        return getattr(self, name)


@dataclass(frozen=True)
class Parse:
    """Result of a parse attempt: the CarKey, unresolved fragments, grammar version and an ok flag."""

    key: CarKey
    unresolved: tuple[str, ...]
    grammar_version: str
    ok: bool = True
