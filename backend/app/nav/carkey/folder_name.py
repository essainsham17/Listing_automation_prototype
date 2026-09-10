"""Parses library folder names: model folders into CarKeys and variant leaves into colours and tag."""

from __future__ import annotations

import re
from dataclasses import replace

from app.nav.carkey import grammar
from app.nav.carkey.types import Parse, Silent
from app.nav.vocab import normalize

FOLDER_GRAMMAR_VERSION = "carkey.folder_name/1"

_INSIDE = re.compile(r"\bINSIDE?\b", re.IGNORECASE)

_TRAILING_RUN = re.compile(r"[\s\-]+([A-Za-z0-9]+)\s*\Z")

_TRAILING_YEAR = re.compile(r"[\s\-]+(\d{2}|\d{4})\s*\Z")

_EDGE_JUNK = " -\t. "


def _tidy(text: str | None) -> str:
    """Normalises whitespace and strips spaces, hyphens, tabs and dots from both ends of the text."""
    return normalize.clean(normalize.clean(text).strip(_EDGE_JUNK))


def parse_model_folder(segment: str | None) -> Parse:
    """Parses a model folder name with the car grammar, forcing colours Silent and noting any it stated."""
    parse = grammar.parse_car_string(segment)

    stated_colour = tuple(
        field.raw for field in (parse.key.exterior, parse.key.interior)
        if getattr(field, "raw", "")
    )
    unresolved = parse.unresolved
    if stated_colour:
        unresolved += (
            f"model folder name states colour {' / '.join(dict.fromkeys(stated_colour))!r}; "
            "ignored, because the variant folders below it hold the colour that "
            "was actually photographed",)

    key = replace(parse.key, exterior=Silent(), interior=Silent())
    return Parse(key=key, unresolved=unresolved,
                 grammar_version=FOLDER_GRAMMAR_VERSION, ok=parse.ok)


def parse_leaf(segment: str | None) -> tuple[str | None, str | None, str | None]:
    """Splits a leaf name on INSIDE into exterior, interior and VIN tag; colours None when it cannot split."""
    name = normalize.clean(segment)
    if not name:
        return None, None, None

    body = name
    vin_tag: str | None = None

    match = _TRAILING_RUN.search(body)
    if match and normalize.looks_like_vin(match.group(1)):
        vin_tag = normalize.clean(match.group(1)).upper()
        body = body[:match.start()]

    match = _TRAILING_YEAR.search(body)
    if match and normalize.year4(match.group(1)) is not None:
        body = body[:match.start()]

    halves = _INSIDE.split(body, maxsplit=1)
    if len(halves) != 2:
        return None, None, vin_tag

    exterior, interior = _tidy(halves[0]), _tidy(halves[1])
    if not exterior or not interior:
        return None, None, vin_tag
    return exterior, interior, vin_tag
