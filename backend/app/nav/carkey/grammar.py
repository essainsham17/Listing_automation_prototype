"""Parses a hyphenated car string into a CarKey by anchoring on its fuel and transmission tokens."""

from __future__ import annotations

import re
from decimal import Decimal

from app.nav.carkey.types import (CarKey, Designation, FieldValue, Parse,
                                  Silent, Stated, Unparseable)
from app.nav.vocab import normalize

GRAMMAR_VERSION = "carkey.grammar/1"


FUEL_TOKENS: tuple[str, ...] = (
    "PV", "DV", "EV", "HEV", "PHEV", "MHEV",
    "PETROL", "DIESEL", "ELECTRIC", "HYBRID",
    "HYBRID_ELECTRIC", "PLUG_IN_HYBRID_ELECTRIC",
)

TRANSMISSION_TOKENS: tuple[str, ...] = (
    "AT", "MT", "CVT", "IVT",
    "AUTOMATIC", "MANUAL", "MANNULAL",
)

_FUEL_FP = frozenset(normalize.fingerprint(t) for t in FUEL_TOKENS)
_TRANSMISSION_FP = frozenset(normalize.fingerprint(t) for t in TRANSMISSION_TOKENS)

ENGINE_MIN_L = Decimal("0.5")
ENGINE_MAX_L = Decimal("10.0")

_SEGMENT_SEP = "-"

_LEADING_YEAR = re.compile(r"\s*(\d{2}|\d{4})\b(.*)\Z", re.DOTALL)

_EDGE_JUNK = " -\t. "


def _tidy(text: str | None) -> str:
    """Normalises whitespace and strips spaces, hyphens, tabs and dots from both ends of the text."""
    return normalize.clean(normalize.clean(text).strip(_EDGE_JUNK))


def _stated(text: str) -> FieldValue[str]:
    """Wraps tidied text as a Stated field with identical value and raw, or Silent if it is empty."""
    tidy = _tidy(text)
    return Stated(value=tidy, raw=tidy) if tidy else Silent()


def _is_fuel(fingerprint: str) -> bool:
    """Returns whether a fingerprint is one of the fuel anchor tokens."""
    return fingerprint in _FUEL_FP


def _is_transmission(fingerprint: str) -> bool:
    """Returns whether a fingerprint is one of the transmission anchor tokens."""
    return fingerprint in _TRANSMISSION_FP


def _find_anchors(fingerprints: list[str]) -> tuple[int, int, int] | None:
    """Locates the last fuel token of a run and the transmission run after it, returning indices or None."""
    index = 0
    total = len(fingerprints)
    while index < total:
        if not _is_fuel(fingerprints[index]):
            index += 1
            continue

        fuel = index
        while fuel + 1 < total and _is_fuel(fingerprints[fuel + 1]):
            fuel += 1

        transmission = None
        for position in range(fuel + 1, total):
            if _is_transmission(fingerprints[position]):
                transmission = position
                break

        if transmission is None:
            index = fuel + 1
            continue

        run_end = transmission
        while run_end + 1 < total and _is_transmission(fingerprints[run_end + 1]):
            run_end += 1
        return fuel, transmission, run_end

    return None


def _read_engine(window: list[str]) -> tuple[FieldValue[Decimal], tuple[str, ...]]:
    """Reads the anchor window as engine litres: Silent if empty, Unparseable unless one number in 0.5-10."""
    stated = [segment for segment in window if _tidy(segment)]
    if not stated:
        return Silent(), ()

    if len(stated) > 1:
        joined = _SEGMENT_SEP.join(_tidy(s) for s in stated)
        return (Unparseable(raw=joined),
                (f"engine window holds {len(stated)} segments {joined!r}; "
                 "something between the anchors shifted",))

    raw = _tidy(stated[0])
    value = normalize.decimal_engine(raw.replace("_", "."))
    if value is None:
        return Unparseable(raw=raw), (f"engine {raw!r} states no number",)
    if not (ENGINE_MIN_L <= value <= ENGINE_MAX_L):
        return (Unparseable(raw=raw),
                (f"engine {raw!r} is outside {ENGINE_MIN_L}-{ENGINE_MAX_L} litres "
                 "and is not a displacement",))
    return Stated(value=value, raw=raw), ()


def _split_year(segment: str) -> tuple[int | None, str, str]:
    """Reads a leading year from a tail segment, returning the year, its raw text and any trailing text."""
    match = _LEADING_YEAR.fullmatch(segment or "")
    if not match:
        return None, "", ""
    year = normalize.year4(match.group(1))
    if year is None:
        return None, "", ""
    return year, match.group(1), _tidy(match.group(2))


def _take_year(tail: list[str]) -> tuple[int | None, str, str, list[str]]:
    """Takes the year from the last or second-last tail segment and returns it with suffix and leftovers."""
    if not tail:
        return None, "", "", tail

    year, year_raw, trailing = _split_year(tail[-1])
    if year is not None:
        return year, year_raw, trailing, tail[:-1]

    if len(tail) >= 2:
        year, year_raw, trailing = _split_year(tail[-2])
        if year is not None and not trailing:
            return year, year_raw, _tidy(tail[-1]), tail[:-2]

    return None, "", "", tail


def _read_colours(colours: list[str]) -> tuple[FieldValue[str], FieldValue[str],
                                               tuple[str, ...]]:
    """Reads exterior and interior from one or two colour segments; three or more become Unparseable."""
    stated = [_tidy(c) for c in colours if _tidy(c)]
    if not stated:
        return Silent(), Silent(), ()
    if len(stated) == 1:
        return _stated(stated[0]), Silent(), ()
    if len(stated) == 2:
        return _stated(stated[0]), _stated(stated[1]), ()

    joined = _SEGMENT_SEP.join(stated)
    note = (f"colours {joined!r} do not split into exactly one exterior and "
            "one interior; the boundary is not stated",)
    return Unparseable(raw=joined), Unparseable(raw=joined), note


def _read_designation(head: list[str]) -> tuple[Designation, FieldValue[str],
                                                FieldValue[str], FieldValue[str],
                                                tuple[str, ...]]:
    """Builds the designation from name segments, splitting brand, model and trim when three or more exist."""
    parts = [_tidy(segment) for segment in head]
    parts = [part for part in parts if part]

    designation = Designation.of(_SEGMENT_SEP.join(parts))

    if not parts:
        return designation, Silent(), Silent(), Silent(), ()

    if len(parts) >= 3:
        trim = " ".join(parts[2:])
        return (designation, _stated(parts[0]), _stated(parts[1]),
                _stated(trim), ())

    joined = _SEGMENT_SEP.join(parts)
    unreadable: FieldValue[str] = Unparseable(raw=joined)
    note = (f"name {joined!r} does not split into brand, model and trim; "
            "a brand alias would settle it",)
    return designation, unreadable, unreadable, unreadable, note


def _unanchored(raw: str, reason: str) -> Parse:
    """Builds a not-ok Parse: empty designation, fields Unparseable (Silent if raw is empty), reason noted."""
    unreadable: FieldValue = Unparseable(raw=raw) if raw else Silent()
    key = CarKey(
        designation=Designation.of(""),
        brand=unreadable,
        model_head=unreadable,
        trim=unreadable,
        fuel=unreadable,
        engine_l=unreadable,
        transmission=unreadable,
        model_year=Silent(),
        raw=raw,
    )
    return Parse(key=key, unresolved=(reason,), grammar_version=GRAMMAR_VERSION,
                 ok=False)


def parse_car_string(text: str | None) -> Parse:
    """Parses a Model Code, Product Description or folder name into a CarKey Parse with unresolved notes."""
    raw = normalize.clean(text)
    if not raw:
        return _unanchored("", "empty string states nothing")

    segments = raw.split(_SEGMENT_SEP)
    fingerprints = [normalize.fingerprint(segment) for segment in segments]

    anchors = _find_anchors(fingerprints)
    if anchors is None:
        return _unanchored(
            raw, f"{raw!r} states no fuel token followed by a transmission token, "
                 "so no field can be placed")
    fuel_at, transmission_at, transmission_run_end = anchors

    unresolved: tuple[str, ...] = ()

    head = segments[:fuel_at]

    head_year: int | None = None
    head_year_raw = ""
    if head:
        candidate = normalize.year4(_tidy(head[0]))
        if candidate is not None:
            head_year, head_year_raw = candidate, _tidy(head[0])
            head = head[1:]

    designation, brand, model_head, trim, notes = _read_designation(head)
    unresolved += notes

    fuel = _stated(segments[fuel_at])
    transmission = _stated(segments[transmission_at])
    for repeated in segments[transmission_at + 1:transmission_run_end + 1]:
        unresolved += (f"repeated transmission token {_tidy(repeated)!r} ignored; "
                       "it is not the exterior colour",)

    engine, notes = _read_engine(segments[fuel_at + 1:transmission_at])
    unresolved += notes

    tail = [segment for segment in segments[transmission_run_end + 1:]
            if _tidy(segment)]

    vin_raw = ""
    remaining: list[str] = []
    for segment in tail:
        if normalize.looks_like_vin(segment):
            if vin_raw:
                unresolved += (f"second vin-shaped segment {_tidy(segment)!r} ignored",)
            else:
                vin_raw = _tidy(segment).upper()
        else:
            remaining.append(segment)

    year, year_raw, suffix, colours = _take_year(remaining)
    if year is None and head_year is not None:
        year, year_raw = head_year, head_year_raw

    exterior, interior, notes = _read_colours(colours)
    unresolved += notes

    key = CarKey(
        designation=designation,
        brand=brand,
        model_head=model_head,
        trim=trim,
        fuel=fuel,
        engine_l=engine,
        transmission=transmission,
        model_year=Stated(value=year, raw=year_raw) if year is not None else Silent(),
        exterior=exterior,
        interior=interior,
        vin_tag=Stated(value=vin_raw, raw=vin_raw) if vin_raw else Silent(),
        variant_suffix=_stated(suffix),
        raw=raw,
    )
    return Parse(key=key, unresolved=unresolved,
                 grammar_version=GRAMMAR_VERSION, ok=bool(designation))
