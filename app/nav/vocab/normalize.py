"""Mechanical car-string helpers: cleaning, fingerprints, tokens, engine size, model year, VIN shape."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SPACES = re.compile(r"\s+")


def clean(text: str | None) -> str:
    """Returns NFKC-folded text with whitespace runs collapsed and ends trimmed; empty string for None."""
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", str(text))
    return _SPACES.sub(" ", folded).strip()


def fingerprint(text: str | None) -> str:
    """Returns the lowercase alphanumeric-only comparison key for a string."""
    return _NON_ALNUM.sub("", clean(text).lower())


def tokens(text: str | None) -> tuple[str, ...]:
    """Returns the ordered lowercase alphanumeric word tokens of a string."""
    return tuple(t for t in _NON_ALNUM.sub(" ", clean(text).lower()).split() if t)


def decimal_engine(text: str | None) -> Decimal | None:
    """Parses the first number in an engine string as a Decimal, accepting comma decimals; None if absent."""
    raw = clean(text).lower().replace(",", ".")
    match = re.search(r"\d+(?:\.\d+)?", raw)
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def year4(text: str | None) -> int | None:
    """Parses a two or four digit model year into a four digit year within 1990-2100, else None."""
    raw = clean(text)
    if not re.fullmatch(r"\d{2}|\d{4}", raw):
        return None
    value = int(raw)
    if len(raw) == 2:
        value += 2000
    return value if 1990 <= value <= 2100 else None


def looks_like_vin(text: str | None) -> bool:
    """Returns whether text is a 15-17 character alphanumeric run containing both letters and digits."""
    raw = clean(text).upper()
    if not re.fullmatch(r"[A-Z0-9]{15,17}", raw):
        return False
    return bool(re.search(r"\d", raw) and re.search(r"[A-Z]", raw))
