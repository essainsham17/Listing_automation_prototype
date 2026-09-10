"""Builds Add Car form fields from a Product Description, resolving colours to catalog ids."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from app.color_match import resolve_color
from app.description_parser import parse_description
from app.folder_colors import colour_options, parse_folder_colors
from app.taxonomy import parse_spec_catalog

logger = logging.getLogger(__name__)

SPEC_CATALOG_PATH = Path(__file__).parent.parent / "data" / "reference" / "specification_catalog.sample.json"


@lru_cache(maxsize=1)
def _spec_catalog():
    """Loads and caches the specification catalog from the sample JSON, returning None on failure."""
    try:
        return parse_spec_catalog(json.loads(SPEC_CATALOG_PATH.read_text(encoding="utf-8")))
    except Exception as e:
        logger.warning("vehicle_fields: could not load the specification catalog (%s) — "
                       "colours will pass through unresolved", e)
        return None


def _resolution_payload(raw, parent, method_prefix: str = "") -> dict | None:
    """Resolves a colour within a parent to raw, status, name, id, method and candidates, or None if empty."""
    if not raw:
        return None
    catalog = _spec_catalog()
    if catalog is None:
        return {"raw": raw, "status": "missing", "name": None, "id": None,
                "method": f"{method_prefix}no_catalog", "candidates": []}
    r = resolve_color(raw, parent, catalog)
    return {"raw": raw, "status": r.status, "name": r.name, "id": r.id,
            "method": f"{method_prefix}{r.method}", "candidates": list(r.candidates or [])}


def _colours_from_folder(folder_path, parsed: dict) -> dict:
    """Resolves folder-name colours, or marks both ambiguous with the choices when several colour folders exist."""
    out = {"exterior_color": None, "interior_color": None, "color_choices": []}
    if not folder_path:
        return out

    folder = Path(folder_path)
    options = colour_options(folder)

    if len(options) > 1:
        out["color_choices"] = options
        for key, parent in (("exterior_color", "Color"), ("interior_color", "Interior Color")):
            out[key] = {"raw": None, "status": "ambiguous", "name": None, "id": None,
                        "method": "folder_ambiguous", "candidates": []}
        logger.info("vehicle_fields: %s has %d colour variants and the description names no "
                    "colour — leaving both colours for the reviewer", folder.name, len(options))
        return out

    exterior, interior = parse_folder_colors(folder.name)
    if not (exterior and interior):
        return out

    out["exterior_color"] = _resolution_payload(exterior, "Color", "folder_")
    out["interior_color"] = _resolution_payload(interior, "Interior Color", "folder_")
    logger.info("vehicle_fields: read colours %r / %r off the folder name %s",
                exterior, interior, folder.name)
    return out


def build_vehicle_fields(description: str, model_number: str | None = None,
                          folder_path: str | None = None,
                          exterior_color: str | None = None,
                          interior_color: str | None = None) -> dict:
    """Builds form fields from a description, preferring passed colours and using folder colours as fallback."""
    parsed = parse_description(description)

    exterior = _resolution_payload(exterior_color or parsed.get("exterior_color"), "Color")
    interior = _resolution_payload(interior_color or parsed.get("interior_color"), "Interior Color")

    color_choices: list[dict] = []
    if folder_path and not (exterior and interior):
        from_folder = _colours_from_folder(folder_path, parsed)
        color_choices = from_folder["color_choices"]
        exterior = exterior or from_folder["exterior_color"]
        interior = interior or from_folder["interior_color"]

    unresolved = [
        label for label, res in (("exterior colour", exterior), ("interior colour", interior))
        if res and res["status"] != "matched"
    ]

    return {
        "stock_id": (description or "").strip() or model_number,
        "model_number": model_number,
        "brand": parsed.get("brand"),
        "model": parsed.get("model"),
        "trim": parsed.get("trim"),
        "year": parsed.get("year"),
        "fuel_type": parsed.get("fuel_type"),
        "engine_size": parsed.get("engine_size"),
        "transmission": parsed.get("transmission"),
        "exterior_color": exterior,
        "interior_color": interior,
        "color_choices": color_choices,
        "unresolved_colors": unresolved,
        "parsed_by": parsed.get("_source"),
    }
