"""Resolves marketing colour names to catalog colours, trying rules first and the model second."""

from __future__ import annotations

import json
import logging
import os


from app import config, resolution_cache
from app.taxonomy import Resolution, SpecCatalog, primary_exterior_color, resolve_spec_value

logger = logging.getLogger(__name__)

NO_MATCH = "NO_MATCH"


def _schema(option_names: list[str]) -> dict:
    """Builds the strict JSON schema restricting the model's choice to catalog names or NO_MATCH."""
    return {
        "name": "color_match",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["choice"],
            "properties": {
                "choice": {"type": "string", "enum": option_names + [NO_MATCH]},
            },
        },
    }


def match_color_with_ai(raw_value: str, parent: str, catalog: SpecCatalog) -> Resolution:
    """Checks the cache, then asks the model to pick a catalog colour, caching verified answers or no match."""
    options = catalog.for_parent(parent)
    if not options:
        return Resolution(parent, raw_value, "missing", method="unknown_parent")

    names = [o.name for o in options]

    cached = resolution_cache.lookup(raw_value, parent, valid_names=set(names))
    if cached is not None:
        if cached["status"] == "matched":
            return Resolution(parent, raw_value, "matched", id=cached["id"], name=cached["name"],
                              confidence=0.85, method="ai_cached")
        return Resolution(parent, raw_value, "missing", method="ai_cached_no_match")

    prompt = (
        f'A car dealer\'s system recorded the {parent.lower()} of a vehicle as: "{raw_value}"\n\n'
        f"That is usually a manufacturer's MARKETING name. Which of the following "
        f"plain colours does it actually refer to?\n\n"
        + "\n".join(f"- {n}" for n in names)
        + f"\n\nAnswer with exactly one of those names, or {NO_MATCH} if none of them "
          f"is a fair description of it. Do not stretch — {NO_MATCH} is the correct "
          f"answer when the colour genuinely is not in the list."
    )

    from app.extract import small_text_completion

    try:
        content, finish_reason, refusal = small_text_completion(
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_schema", "json_schema": _schema(names)},
            max_tokens=config.DESCRIPTION_PARSER_MAX_TOKENS,
            log_context="color_match")
    except RuntimeError as e:
        logger.warning("color_match: %s", e)
        return Resolution(parent, raw_value, "missing", method="no_api_key")

    if not content:
        logger.warning("color_match: no content for %r (finish_reason=%s, refusal=%s)",
                       raw_value, finish_reason, refusal)
        return Resolution(parent, raw_value, "missing", method="llm_error")

    try:
        choice = (json.loads(content) or {}).get("choice")
    except Exception as e:
        logger.warning("color_match: unparseable reply for %r (%s)", raw_value, e)
        return Resolution(parent, raw_value, "missing", method="llm_error")

    if not choice or choice == NO_MATCH:
        resolution_cache.record(raw_value, parent, "missing", None, None, method="llm_no_match")
        return Resolution(parent, raw_value, "missing", method="llm_no_match")

    for opt in options:
        if opt.name == choice:
            resolution_cache.record(raw_value, parent, "matched", opt.name, opt.id, method="llm")
            return Resolution(parent, raw_value, "matched", id=opt.id, name=opt.name,
                              confidence=0.85, method="llm")

    logger.warning("color_match: model returned %r, which is not in the %s catalog — discarding",
                   choice, parent)
    return Resolution(parent, raw_value, "missing", method="llm_invalid_choice")


def resolve_color(raw_value: str, parent: str, catalog: SpecCatalog) -> Resolution:
    """Reduces exterior colours to the body colour, tries deterministic matching, then the model if enabled."""
    if not (raw_value or "").strip():
        return Resolution(parent, raw_value, "missing", method="empty")

    value = primary_exterior_color(raw_value) if parent == "Color" else raw_value

    deterministic = resolve_spec_value(value, parent, catalog)
    if deterministic.status == "matched" or not config.COLOR_MATCH_USE_AI:
        return deterministic

    return match_color_with_ai(value, parent, catalog)
