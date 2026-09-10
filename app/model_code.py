"""Parses a hyphenated Model Code by anchoring on its fuel and transmission tokens."""

import re

FUEL_MAP = {"PV": "Petrol", "PHEV": "Hybrid", "HEV": "Hybrid", "MHEV": "Hybrid",
            "EV": "Electric", "DV": "Diesel",
            "PETROL": "Petrol", "DIESEL": "Diesel", "ELECTRIC": "Electric",
            "HYBRID": "Hybrid", "HYBRID_ELECTRIC": "Hybrid",
            "PLUG_IN_HYBRID_ELECTRIC": "Hybrid"}
TRANS_MAP = {"AT": "Automatic", "MT": "Manual", "CVT": "CVT", "IVT": "IVT",
             "AUTOMATIC": "Automatic", "MANUAL": "Manual", "MANNULAL": "Manual"}

KNOWN_BRANDS = {
    "TOYOTA", "LEXUS", "KIA", "MG", "NISSAN", "HONDA", "ROX", "GAC", "BYD",
    "GREATWALL", "GWM", "SUZUKI", "CHANGAN", "BAW", "DONGFENG", "BMW",
    "HYUNDAI", "MITSUBISHI", "FORD", "CHEVROLET", "JEEP", "MERCEDES",
    "AUDI", "VOLKSWAGEN", "PORSCHE", "JAGUAR", "LANDROVER", "LAND ROVER",
    "PEUGEOT", "RENAULT", "GEELY", "HAVAL", "JAC", "CHERY", "MAXUS",
    "FOTON", "ISUZU", "MAZDA", "SUBARU", "VOLVO", "JETOUR", "TANK",
}


def _find_first(parts: list[str], vocab: dict, start: int = 0) -> int | None:
    """Returns the index of the first segment from start whose upper-cased value is in vocab, else None."""
    for i in range(start, len(parts)):
        if parts[i].strip().upper() in vocab:
            return i
    return None


def parse_model_code(code: str) -> dict | None:
    """Returns a dict of stock id, brand, model, trim, fuel, engine, transmission, colours, year, or None."""
    stem = (code or "").strip()
    parts = [p for p in stem.split("-")]
    if len(parts) < 5:
        return None

    fuel_idx = _find_first(parts, FUEL_MAP)
    if fuel_idx is None:
        return None
    trans_idx = _find_first(parts, TRANS_MAP, start=fuel_idx + 1)
    if trans_idx is None:
        return None
    colour_start = trans_idx + 1
    while colour_start < len(parts) and parts[colour_start].strip().upper() in TRANS_MAP:
        colour_start += 1

    between = parts[fuel_idx + 1:trans_idx]
    if len(between) != 1:
        return None
    engine = between[0].strip().replace("_", ".")
    if not engine:
        engine = None
    elif not re.fullmatch(r"\d+(\.\d+)?", engine):
        return None

    free_before = list(parts[:fuel_idx])
    free_after = list(parts[colour_start:])

    def _as_year(token: str) -> int | None:
        """Converts a two-digit or 20xx token to a four-digit year, returning None for anything else."""
        token = token.strip()
        if re.fullmatch(r"\d{2}", token):
            return 2000 + int(token)
        if re.fullmatch(r"20\d{2}", token):
            return int(token)
        return None

    year = None
    if free_before and _as_year(free_before[0]) is not None:
        year = _as_year(free_before[0])
        free_before = free_before[1:]
    elif free_after and _as_year(free_after[-1]) is not None:
        year = _as_year(free_after[-1])
        free_after = free_after[:-1]

    if not free_before:
        return None
    if len(free_before) >= 3:
        brand, model, trim = free_before[0], free_before[1], " ".join(free_before[2:])
    elif len(free_before) == 2:
        if free_before[0].strip().upper() in KNOWN_BRANDS:
            brand, model, trim = free_before[0], free_before[1], ""
        else:
            brand, model, trim = "", free_before[0], free_before[1]
    else:
        brand, model, trim = "", free_before[0], ""

    exterior_color = free_after[0].strip() if len(free_after) >= 1 else None
    interior_color = free_after[1].strip() if len(free_after) >= 2 else None

    return {
        "stock_id": stem,
        "brand": brand.strip().title(),
        "model": model.strip().title(),
        "trim": trim.strip().title(),
        "fuel_type": FUEL_MAP[parts[fuel_idx].strip().upper()],
        "engine_size": engine,
        "transmission": TRANS_MAP[parts[trans_idx].strip().upper()],
        "exterior_color": exterior_color,
        "interior_color": interior_color,
        "year": year,
    }
