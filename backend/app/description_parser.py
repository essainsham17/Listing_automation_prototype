"""Parses Salesforce Product Descriptions into vehicle fields via an LLM with a regex fallback."""

from __future__ import annotations

import json
import logging
import os


from app import config
from app.model_code import parse_model_code

logger = logging.getLogger(__name__)

FIELDS = ("brand", "model", "trim", "year", "exterior_color", "interior_color",
          "fuel_type", "engine_size", "transmission")

_SYSTEM = """You read vehicle Product Description codes from a car dealer's \
Salesforce export and return the structured fields they encode.

These codes follow a loose convention that VARIES. Do not assume a fixed \
segment order. Read the string the way a person familiar with cars would.

Rules:
- Return ONLY what the string actually contains. Never guess a value that \
is not there — use null for anything absent.
- Colours: return them EXACTLY as written, in the source's own words \
("PRECIOUS WHITE PEARL", "SONIC QUARTZ", "CHAMOIS"). Do NOT simplify them \
to a basic colour; a later step does that against a fixed list.
- Exterior colour comes before interior colour when both are present.
- A two-tone exterior ("WHITE BODY & BLACK ROOF") is ONE exterior value; \
keep it whole.
- year: return a 4-digit integer. A trailing 2-digit token like "26" means \
2026, "12" means 2012.
- The brand is often, but not always, first. It is sometimes absent \
entirely (e.g. "ROX 01-..." where ROX 01 is the model).
- A segment that looks like a fuel/transmission token may actually be the \
model name (e.g. Toyota's "CROWN-HEV" model). Use judgement.
- fuel_type: one of Petrol, Diesel, Hybrid, Electric — or null.
- transmission: one of Automatic, Manual, CVT, IVT — or null.
- engine_size: the litre figure as written, without a unit ("2.4", "3.5").

Return a single JSON object with exactly these keys:
brand, model, trim, year, exterior_color, interior_color, fuel_type, \
engine_size, transmission"""

_EXAMPLES = """Examples:

"TOYOTA-CROWN-HEV-HEV-2.5-AT-PRECIOUS WHITE PEARL-BLACK-26"
{"brand":"Toyota","model":"Crown HEV","trim":null,"year":2026,\
"exterior_color":"PRECIOUS WHITE PEARL","interior_color":"BLACK",\
"fuel_type":"Hybrid","engine_size":"2.5","transmission":"Automatic"}

"ROX 01-6 SEATER-PHEV-1.5-AT-BLACK-ORANGE-26"
{"brand":null,"model":"ROX 01","trim":"6 SEATER","year":2026,\
"exterior_color":"BLACK","interior_color":"ORANGE","fuel_type":"Hybrid",\
"engine_size":"1.5","transmission":"Automatic"}

"26-LEXUS-LEXUS LX700-LX7HU-HEV-3.5-AT-BLACK-CHAMOIS"
{"brand":"Lexus","model":"LX700","trim":"LX7HU","year":2026,\
"exterior_color":"BLACK","interior_color":"CHAMOIS","fuel_type":"Hybrid",\
"engine_size":"3.5","transmission":"Automatic"}

"TOYOTA-HILUX-H24AF-DV-2.4L-AT-PLATINUM WHITE-MAROON-26"
{"brand":"Toyota","model":"Hilux","trim":"H24AF","year":2026,\
"exterior_color":"PLATINUM WHITE","interior_color":"MAROON",\
"fuel_type":"Diesel","engine_size":"2.4","transmission":"Automatic"}

"KIA-SELTOS-LUXURY-PV-1.5-AT-WHITE BODY & BLACK ROOF-BLACK-25"
{"brand":"Kia","model":"Seltos","trim":"LUXURY","year":2025,\
"exterior_color":"WHITE BODY & BLACK ROOF","interior_color":"BLACK",\
"fuel_type":"Petrol","engine_size":"1.5","transmission":"Automatic"}"""


def _schema() -> dict:
    """Returns the strict JSON schema for the vehicle description reply, with every field nullable."""
    nullable_str = {"type": ["string", "null"]}
    return {
        "name": "vehicle_description",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": list(FIELDS),
            "properties": {
                "brand": nullable_str,
                "model": nullable_str,
                "trim": nullable_str,
                "year": {"type": ["integer", "null"]},
                "exterior_color": nullable_str,
                "interior_color": nullable_str,
                "fuel_type": {"type": ["string", "null"],
                              "enum": ["Petrol", "Diesel", "Hybrid", "Electric", None]},
                "engine_size": nullable_str,
                "transmission": {"type": ["string", "null"],
                                 "enum": ["Automatic", "Manual", "CVT", "IVT", None]},
            },
        },
    }


def _from_regex(description: str) -> dict:
    """Parses the description with parse_model_code and returns the fields tagged with _source regex."""
    parsed = parse_model_code(description) or {}
    return {
        "brand": parsed.get("brand") or None,
        "model": parsed.get("model") or None,
        "trim": parsed.get("trim") or None,
        "year": parsed.get("year"),
        "exterior_color": parsed.get("exterior_color"),
        "interior_color": parsed.get("interior_color"),
        "fuel_type": parsed.get("fuel_type"),
        "engine_size": parsed.get("engine_size"),
        "transmission": parsed.get("transmission"),
        "_source": "regex",
    }


def parse_description(description: str) -> dict:
    """Extracts vehicle fields from a description via the LLM, using the regex parser if AI is off or fails."""
    text = (description or "").strip()
    if not text:
        return {**{f: None for f in FIELDS}, "_source": "empty"}

    if not config.DESCRIPTION_PARSER_USE_AI:
        return _from_regex(text)

    from app.extract import small_text_completion

    try:
        content, finish_reason, refusal = small_text_completion(
            messages=[
                {"role": "system", "content": _SYSTEM + "\n\n" + _EXAMPLES},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_schema", "json_schema": _schema()},
            max_tokens=config.DESCRIPTION_PARSER_MAX_TOKENS,
            log_context="description_parser")
    except RuntimeError as e:
        logger.warning("description_parser: %s — falling back to the regex parser", e)
        return _from_regex(text)

    if not content:
        logger.warning("description_parser: no content (finish_reason=%s, refusal=%s) "
                       "— falling back to the regex parser", finish_reason, refusal)
        return _from_regex(text)

    try:
        parsed = json.loads(content.strip())
    except Exception as e:
        logger.warning("description_parser: unparseable reply (%s) — falling back to the regex parser", e)
        return _from_regex(text)

    if not isinstance(parsed, dict):
        logger.warning("description_parser: model returned %s, not an object — using the regex parser", type(parsed))
        return _from_regex(text)

    out = {f: parsed.get(f) for f in FIELDS}
    for k, v in list(out.items()):
        if isinstance(v, str) and not v.strip():
            out[k] = None
    out["_source"] = "ai"
    return out
