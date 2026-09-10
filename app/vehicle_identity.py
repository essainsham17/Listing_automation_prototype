"""Merges model code and spec sheet readings of brand, model, trim and year, flagging conflicts."""

from __future__ import annotations

import re

COMPARED_FIELDS = ("brand", "model", "trim", "year")


def _norm(value) -> str:
    """Lowercases a value and strips non-alphanumeric characters for comparison, returning empty for None."""
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def _clean(value):
    """Returns the stripped string form of a value, or None when it is None or empty."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def merge_vehicle_identity(parsed_code: dict | None, extraction: dict | None) -> dict:
    """Merges model code and spec sheet values per field, preferring the sheet and flagging conflicts."""
    parsed_code = parsed_code or {}
    extraction = extraction or {}
    out: dict[str, dict] = {}

    for field in COMPARED_FIELDS:
        from_code = _clean(parsed_code.get(field))
        from_sheet = _clean(extraction.get(field))

        if from_code is None and from_sheet is None:
            continue

        if from_code is not None and from_sheet is not None:
            agreed = _norm(from_code) == _norm(from_sheet)
            out[field] = {
                "value": from_sheet,
                "source": "agreed" if agreed else "conflict",
                "conflict": not agreed,
                "model_code": from_code,
                "spec_sheet": from_sheet,
            }
        elif from_sheet is not None:
            out[field] = {"value": from_sheet, "source": "spec_sheet", "conflict": False,
                          "model_code": None, "spec_sheet": from_sheet}
        else:
            out[field] = {"value": from_code, "source": "model_code", "conflict": False,
                          "model_code": from_code, "spec_sheet": None}

    return out


def conflict_summary(identity: dict) -> str | None:
    """Returns a reviewer sentence listing each field where sheet and code disagree, or None."""
    conflicts = [(f, d) for f, d in identity.items() if d.get("conflict")]
    if not conflicts:
        return None
    parts = [f"{f} (sheet says \"{d['spec_sheet']}\", code says \"{d['model_code']}\")"
             for f, d in conflicts]
    return (
        "The spec sheet and the Model Code disagree on "
        + "; ".join(parts)
        + ". The spec sheet's reading was used — confirm it before publishing."
    )
