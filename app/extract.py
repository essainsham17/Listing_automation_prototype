"""Extracts listing data from spec-sheet PDFs with an LLM and matches it to form fields and checkboxes."""

import contextvars
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langfuse import observe
from langfuse.openai import OpenAI

from app import config
from app.pdf_to_images import pdf_to_page_images_b64
from app.pdf_text import extract_text_layer
from app.recovery import recover_truncated_json

logger = logging.getLogger(__name__)

MODEL = config.EXTRACTION_MODEL
BASE_URL = config.EXTRACTION_BASE_URL
GROQ_MODEL = config.GROQ_MODEL
GROQ_BASE_URL = config.GROQ_BASE_URL

SCHEMA_PATH = Path(__file__).parent.parent / "data" / "schema.json"
_FORM_SCHEMA = json.loads(SCHEMA_PATH.read_text())
FEATURE_GROUPS = _FORM_SCHEMA["featureGroups"]

PROMPT = """You are extracting structured vehicle data from a Legend Motors PDI/spec \
sheet for our car listing system. You'll see every page of the document as images \
— some pages are the spec table, some are just vehicle photos. Ignore the photo \
pages entirely; only extract from table/text content.

Rules:
- Only use information visibly present in the document. Never invent a value.
- For "raw_specifications": list EVERY row of the spec table — transmission, drive \
  type, engine, seats, colors, wheel size, whatever the sheet actually shows — as \
  {"label": ..., "value": ...} pairs, transcribed close to verbatim. Do not try to \
  match these to any predefined field list or category — that mapping happens \
  separately, by a different process that reads the actual form being filled. Just \
  capture every spec row the document states, completely.
- Engine size: transcribe as shown, but if the sheet only gives displacement in cc \
  (e.g. "1,497"), also convert to liters (liters ≈ cc / 1000, rounded to 1 decimal) \
  since that's the more commonly requested unit — e.g. value "1.5L (1,497cc)".
- Horsepower: transcribe as shown; if the sheet shows a combined "power/rpm" cell \
  (e.g. "115/6,300"), you may still capture it verbatim, but also note the power \
  figure alone somewhere in the value (e.g. "115 hp (115/6,300 rpm)").
- Hybrid/PHEV horsepower: if the sheet gives a combined SYSTEM power in kW (common \
  for hybrids — e.g. "Total power/torque of power system: 350kW/740N.m") but no \
  horsepower figure directly, convert kW to hp (hp ≈ kW × 1.341) and include both, \
  same convention as the cc-to-liters conversion above — e.g. value \
  "469 hp (350kW combined system power)". Only do this when a kW figure is actually \
  on the sheet — never invent one.
- Regional specification: if the sheet mentions explicit certification/market markers \
  anywhere (e.g. "China 6b", "China VI", "HiCar", "CCC certified"), capture that as \
  its own {"label": "Regional Specification", "value": ...} row even if the sheet has \
  no row explicitly labeled "Regional Specification" — e.g. value "Chinese Specs \
  (China 6b / CCC certified)". Only when those markers are actually present on the \
  sheet — never guess a region without one.
- IMPORTANT: some spec sheets mark features with a filled bullet (●) for \
  "standard/included" versus a hollow circle (○) for "optional, not included \
  as standard". Only include a feature in "raw_features" if it's marked as \
  standard/included (●) — a hollow-circle (○) item is NOT present on this car \
  and must be omitted, even though it's listed on the sheet.
- For "raw_features": list EVERY standard/included feature from the sheet, \
  transcribed close to verbatim. Do not try to match, filter, or map these to \
  any predefined category or vocabulary — that mapping happens separately. \
  Just capture what the document actually says, completely.
- If something can't be determined at all, omit it rather than guessing, and add \
  a note to low_confidence_fields.
"""


def _submit_with_context(pool: ThreadPoolExecutor, fn, *args, **kwargs):
    """Submits a function to a thread pool so it runs inside a copy of the caller's contextvars context."""
    ctx = contextvars.copy_context()
    return pool.submit(ctx.run, fn, *args, **kwargs)


def build_response_schema() -> dict:
    """Returns the strict JSON schema the extraction response must follow, including raw specs and raw features."""
    return {
        "name": "extracted_car_listing",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "model": {"type": "string"},
                "trim": {"type": "string"},
                "year": {"type": "integer"},
                "raw_specifications": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"label": {"type": "string"}, "value": {"type": "string"}},
                        "required": ["label", "value"],
                        "additionalProperties": False,
                    },
                    "description": "Every spec-table row from the document, verbatim, as "
                                   "{label, value} pairs — do NOT try to match these to any "
                                   "predefined field list, just transcribe what the document says.",
                },
                "description": {"type": "string", "description": "3-4 paragraph marketing description"},
                "raw_features": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Every feature/equipment item marked standard/included on the "
                                   "sheet, verbatim as written — do NOT try to match these to any "
                                   "predefined list, just transcribe what the document says.",
                },
                "low_confidence_fields": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["brand", "model", "trim", "year", "raw_specifications",
                         "description", "raw_features", "low_confidence_fields"],
            "additionalProperties": False,
        },
    }


def _create_completion(client: OpenAI, kwargs: dict, log_context: str = ""):
    """Runs a logged chat completion, catching SDK errors; returns (content, finish_reason, refusal)."""
    t0 = time.monotonic()
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as e:
        logger.warning("%s: request failed after %.2fs: %s", log_context, time.monotonic() - t0, e)
        return None, "sdk_exception", str(e)
    elapsed = time.monotonic() - t0
    usage = getattr(response, "usage", None)
    logger.info(
        "%s: %.2fs, model=%s, tokens=%s",
        log_context, elapsed, kwargs.get("model"),
        f"{usage.total_tokens} (prompt={usage.prompt_tokens}, completion={usage.completion_tokens})" if usage else "unknown",
    )
    return _extract_choice(response)


def _extract_choice(response):
    """Unpacks a response into (content, finish_reason, refusal), reporting an error if it has no choices."""
    if not response.choices:
        upstream_error = getattr(response, "error", None)
        return None, "upstream_error", f"no choices in response (upstream error: {upstream_error!r})"
    choice = response.choices[0]
    return choice.message.content, choice.finish_reason, getattr(choice.message, "refusal", None)


def _canonical_option(value: str, allowed) -> str | None:
    """Returns the allowed option matching value exactly or ignoring case and outer whitespace, else None."""
    if value in allowed:
        return value
    folded = {a.casefold().strip(): a for a in allowed}
    return folded.get(value.casefold().strip())


def _strip_markdown_fences(text: str) -> str:
    """Removes surrounding markdown code fences from model output and trims whitespace."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _build_example_output() -> dict:
    """Returns a sample extraction object with realistic values to show the model the expected JSON shape."""
    return {
        "brand": "Toyota", "model": "Land Cruiser", "trim": "GXR", "year": 2025,
        "raw_specifications": [
            {"label": "Transmission", "value": "Automatic"},
            {"label": "Engine Size", "value": "1.5L"},
            {"label": "Seats", "value": "5"},
        ],
        "description": "A 3-4 paragraph marketing description goes here.",
        "raw_features": ["360 degree camera", "Rear Air Conditioning Vents", "Push Start"],
        "low_confidence_fields": [],
    }


_KNOWN_SYNONYMS = {
    "electronic stability control": ("security", "Vehicle Stability Control"),
    "front parking radar": ("comfort", "Parking sensor front"),
    "rear parking radar": ("comfort", "Parking sensor rear"),
    "high beam assist": ("security", "Auto High Beam Control"),
    "rearview camera": ("comfort", "Rear Camera"),
    "tire pressure": ("comfort", "Tyre pressure warning system"),
    "usb interface": ("infotainment", "USB & Auxiliary Cable"),
    "bluetooth": ("infotainment", "Bluetooth system"),
    "driving mode selection": ("comfort", "Drive Modes"),
    "smart display": ("infotainment", "Touch Screen"),
    "multifunctional steering wheel": ("comfort", "Steering Switches"),
    "one-key start": ("comfort", "Push Start"),
}


def _normalize_tokens(text: str) -> set[str]:
    """Lowercases and splits text into stemmed tokens, adding expansions of known automotive abbreviations."""
    abbreviations = {
        "ac": "air conditioning", "a/c": "air conditioning",
        "tpms": "tire pressure monitoring system",
        "abs": "anti lock braking system",
        "4wd": "four wheel drive", "awd": "all wheel drive",
        "esc": "electronic stability control",
        "usb": "universal serial bus",
        "tire": "tyre",
    }
    text = re.sub(r"[^\w\s]", " ", text.lower())
    tokens = set()
    for word in text.split():
        tokens.add(_stem(word))
        if word in abbreviations:
            tokens.update(_stem(w) for w in abbreviations[word].split())
    return tokens


def _stem(word: str) -> str:
    """Strips a trailing 'ing', 'ed', 'es' or 's' suffix when at least three characters remain."""
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


_KNOWN_ACRONYMS = {
    "tpms": ("comfort", "Tyre pressure warning system"),
    "esc": ("security", "Vehicle Stability Control"),
    "vsc": ("security", "Vehicle Stability Control"),
    "esp": ("security", "Vehicle Stability Control"),
    "vdc": ("security", "Vehicle Stability Control"),
}


def _best_vocab_match(raw_feature: str, threshold: float = 0.6):
    """Returns the best FEATURE_GROUPS (group, label) for a raw feature by synonym, acronym or token overlap."""
    raw_lower = raw_feature.lower()
    for pattern, target in _KNOWN_SYNONYMS.items():
        if pattern in raw_lower:
            return target

    raw_words = set(re.sub(r"[^\w\s]", " ", raw_lower).split())
    for acronym, target in _KNOWN_ACRONYMS.items():
        if acronym in raw_words:
            return target

    raw_tokens = _normalize_tokens(raw_feature)
    if not raw_tokens:
        return None, None
    best_group, best_label, best_score = None, None, 0.0
    for group, labels in FEATURE_GROUPS.items():
        for label in labels:
            label_tokens = _normalize_tokens(label)
            if not label_tokens:
                continue
            overlap = raw_tokens & label_tokens
            union = raw_tokens | label_tokens
            jaccard = len(overlap) / len(union) if union else 0.0
            containment = len(overlap) / min(len(raw_tokens), len(label_tokens))
            score = max(jaccard, containment * 0.85)
            if score > best_score:
                best_score, best_group, best_label = score, group, label
    return (best_group, best_label) if best_score >= threshold else (None, None)


def _best_flat_vocab_match(raw_feature: str, vocabulary: list[str], threshold: float) -> str | None:
    """Returns the flat-vocabulary label best matching a raw feature via synonym, acronym or overlap, or None."""
    vocab_set = set(vocabulary)
    raw_lower = raw_feature.lower()
    for pattern, (_, target_label) in _KNOWN_SYNONYMS.items():
        if pattern in raw_lower and target_label in vocab_set:
            return target_label

    raw_words = set(re.sub(r"[^\w\s]", " ", raw_lower).split())
    for acronym, (_, target_label) in _KNOWN_ACRONYMS.items():
        if acronym in raw_words and target_label in vocab_set:
            return target_label

    raw_tokens = _normalize_tokens(raw_feature)
    if not raw_tokens:
        return None
    best_label, best_score = None, 0.0
    for label in vocabulary:
        label_tokens = _normalize_tokens(label)
        if not label_tokens:
            continue
        overlap = raw_tokens & label_tokens
        union = raw_tokens | label_tokens
        jaccard = len(overlap) / len(union) if union else 0.0
        containment = len(overlap) / min(len(raw_tokens), len(label_tokens))
        score = max(jaccard, containment * 0.85)
        if score > best_score:
            best_score, best_label = score, label
    return best_label if best_score >= threshold else None


def _enrich_features_from_raw(parsed: dict) -> dict:
    """Adds grouped checkbox features matched from raw_features and flags unmatched items as low confidence."""
    raw_list = parsed.get("raw_features", [])
    features = {group: [] for group in FEATURE_GROUPS}
    unmatched = []

    for raw_item in (raw_list if isinstance(raw_list, list) else []):
        if not isinstance(raw_item, str) or not raw_item.strip():
            continue
        group, label = _best_vocab_match(raw_item)
        if group and label not in features[group]:
            features[group].append(label)
        elif not group:
            unmatched.append(raw_item)

    parsed["features"] = features
    if unmatched:
        parsed.setdefault("low_confidence_fields", []).append(
            f"no_matching_checkbox_for: {', '.join(unmatched)}"
        )
    return parsed


def build_feature_match_schema(available_features: list, raw_features: list) -> dict:
    """Returns the enum-constrained schema for matched checkboxes and important/other unmatched features."""
    return {
        "name": "matched_features",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "matched_features": {"type": "array", "items": {"type": "string", "enum": available_features}},
                "important_unmatched_features": {"type": "array", "items": {"type": "string", "enum": raw_features}},
                "other_unmatched_features": {"type": "array", "items": {"type": "string", "enum": raw_features}},
            },
            "required": ["matched_features", "important_unmatched_features", "other_unmatched_features"],
            "additionalProperties": False,
        },
    }


def _call_feature_match_model(client: OpenAI, model: str, raw_features: list, description: str,
                               available_features: list, use_strict_schema: bool, extra_kwargs: dict | None = None):
    """Prompts a model to match checkboxes and tier unmatched raw features, via strict schema or example fallback."""
    vocab_text = ", ".join(available_features)
    prompt = (
        "You are matching a car's actual features (from its spec sheet) to a fixed list "
        "of checkbox options on our listing form. Choose ONLY the options from the allowed "
        "list below that this car genuinely has. Sheets word things differently from our "
        "checkboxes — abbreviations, translated engineering codes, completely different "
        "phrasing for the same physical feature (e.g. a sheet's \"10.25-inch smart display\" "
        "IS a \"Touch Screen\"; \"PUSH STARTING SYSTEM\" IS \"Push Start\"). Match by "
        "MEANING, using your knowledge of automotive terminology, not exact wording. Never "
        "include an option the car doesn't actually have, and never invent an option outside "
        "the allowed list — when genuinely unsure, leave it out.\n\n"
        "Some of the car's real features (from its spec sheet) genuinely have NO "
        "corresponding checkbox in the allowed list at all — e.g. a sunroof, alloy wheels, "
        "or LED headlights when this form has no such checkbox. List those, VERBATIM as "
        "they appear in the car's features below, split into two tiers so a human reviewer "
        "isn't overwhelmed: \"important_unmatched_features\" for things a car buyer would "
        "actually care about and a listing should call out (e.g. a sunroof, premium sound "
        "system, ventilated seats, a notable driver-assist feature), and "
        "\"other_unmatched_features\" for everything else real but minor or generic (routine "
        "trim badges, standard safety mentions, small convenience details). Only include "
        "genuinely real, present features in either list, not things that just didn't fit "
        "well — when unsure whether something is important or minor, put it in "
        "other_unmatched_features.\n\n"
        f"Car's features (verbatim from its spec sheet):\n{json.dumps(raw_features, indent=2)}\n\n"
        f"Car's description:\n{description}\n\n"
        f"Allowed options (choose ONLY from these, matching spelling exactly):\n{vocab_text}\n"
    )
    kwargs = {
        "model": model,
        "max_tokens": 3000,
        "messages": [{"role": "user", "content": prompt}],
        **(extra_kwargs or {}),
    }
    if use_strict_schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": build_feature_match_schema(available_features, raw_features),
        }
    else:
        example = {"matched_features": available_features[:2], "important_unmatched_features": raw_features[:1],
                   "other_unmatched_features": []}
        kwargs["messages"][0]["content"] += (
            "\n\nRespond with ONLY a single JSON object in EXACTLY this shape (both arrays "
            "hold illustrative placeholders — use real matched options / real unmatched raw "
            "features, or empty arrays if none apply):\n"
            f"{json.dumps(example, indent=2)}\nNo markdown fences, no prose before or after."
        )
    attempt = "strict" if use_strict_schema else "fallback"
    return _create_completion(client, kwargs, log_context=f"match_features[{model}, {attempt}]")


def _match_features_single_provider(client: OpenAI, model: str, raw_features: list, description: str,
                                     available_features: list, extra_kwargs: dict | None = None) -> dict:
    """Runs strict then fallback feature matching on one provider and raises RuntimeError if both fail."""
    def _try(use_strict_schema: bool):
        """Runs one feature-matching attempt and returns validated matched and unmatched lists or an error."""
        content, finish_reason, refusal = _call_feature_match_model(
            client, model, raw_features, description, available_features, use_strict_schema, extra_kwargs)
        if not content or not content.strip():
            return None, f"empty content (finish_reason={finish_reason!r}, refusal={refusal!r})"
        try:
            parsed = json.loads(_strip_markdown_fences(content))
        except json.JSONDecodeError as e:
            return None, f"invalid JSON ({e}): {content[:300]!r}"
        if not isinstance(parsed, dict) or not isinstance(parsed.get("matched_features"), list):
            return None, f"expected an object with a 'matched_features' array, got: {json.dumps(parsed)[:300]}"
        available_set = set(available_features)
        raw_set = set(raw_features)
        cleaned_matched = []
        for v in parsed["matched_features"]:
            if not isinstance(v, str):
                continue
            canonical = _canonical_option(v, available_set)
            if canonical is not None:
                cleaned_matched.append(canonical)

        def _clean_unmatched(key: str) -> list:
            """Returns the strings under a response key that are among the raw features, or an empty list."""
            raw_unmatched = parsed.get(key, [])
            return [v for v in raw_unmatched if isinstance(v, str) and v in raw_set] if isinstance(raw_unmatched, list) else []

        return {
            "matched_features": cleaned_matched,
            "important_unmatched_features": _clean_unmatched("important_unmatched_features"),
            "other_unmatched_features": _clean_unmatched("other_unmatched_features"),
        }, None

    parsed, error1 = _try(use_strict_schema=True)
    if parsed is None:
        parsed, error2 = _try(use_strict_schema=False)
        if parsed is None:
            raise RuntimeError(f"Attempt 1 (strict schema): {error1}\nAttempt 2 (fallback): {error2}")
    return parsed


@observe()
def match_features_with_llm(raw_features: list, description: str, available_features: list) -> dict:
    """Matches raw features to checkboxes via token overlap and parallel LLMs; returns matches and suggestions."""
    if not available_features:
        return {"matched_features": [], "suggested_features": []}

    deterministic_matches = {}
    for raw_item in raw_features:
        if not isinstance(raw_item, str) or not raw_item.strip():
            continue
        label = _best_flat_vocab_match(raw_item, available_features, config.FEATURE_MATCH_DECISIVE_THRESHOLD)
        if label:
            deterministic_matches[raw_item] = label
    leftover_raw_features = [f for f in raw_features if f not in deterministic_matches]
    if deterministic_matches:
        logger.info("match_features: %d/%d matched deterministically by token overlap, %d left for the LLM",
                     len(deterministic_matches), len(raw_features), len(leftover_raw_features))
    if not leftover_raw_features:
        return {"matched_features": sorted(set(deterministic_matches.values())), "suggested_features": []}

    providers = _match_providers()

    results, errors = {}, {}
    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        futures = {
            _submit_with_context(pool, _match_features_single_provider, client, model, leftover_raw_features,
                                 description, available_features, extra_kwargs): name
            for name, client, model, extra_kwargs in providers
        }
        for future, name in futures.items():
            try:
                results[name] = future.result()
                logger.info("match_features[%s]: %d matches, %d important unmatched, %d other unmatched",
                            name, len(results[name]["matched_features"]),
                            len(results[name]["important_unmatched_features"]),
                            len(results[name]["other_unmatched_features"]))
            except Exception as e:
                errors[name] = str(e)
                logger.warning("match_features[%s]: provider failed: %s", name, e)

    if not results:
        detail = "\n".join(f"{name}: {err}" for name, err in errors.items())
        raise RuntimeError(f"Feature matching failed on all providers.\n{detail}")
    if errors:
        logger.warning("match_features: degraded — %d/%d provider(s) failed (%s), "
                        "returning results from the rest", len(errors), len(providers), ", ".join(errors))

    matched_sets = [set(r["matched_features"]) for r in results.values()]
    important_sets = [set(r["important_unmatched_features"]) for r in results.values()]
    other_sets = [set(r["other_unmatched_features"]) for r in results.values()]
    unmatched_sets = [imp | oth for imp, oth in zip(important_sets, other_sets)]
    matched_features = sorted(set(deterministic_matches.values()).union(*matched_sets))
    agreed_unmatched = set.intersection(*unmatched_sets) if unmatched_sets else set()
    agreed_important = agreed_unmatched & set().union(*important_sets)
    suggested_features = [
        {"label": label, "important": label in agreed_important}
        for label in sorted(agreed_unmatched, key=lambda l: (l not in agreed_important, l))
    ]
    return {"matched_features": matched_features, "suggested_features": suggested_features}


def build_field_match_schema(available_fields: list[dict]) -> dict:
    """Returns a strict schema with one nullable property per field, enum-constrained for select fields."""
    properties = {}
    for f in available_fields:
        if f["type"] == "select":
            properties[f["id"]] = {"anyOf": [{"type": "string", "enum": f["options"]}, {"type": "null"}]}
        else:
            properties[f["id"]] = {"type": ["string", "null"]}
    return {
        "name": "matched_fields",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": properties,
            "required": [f["id"] for f in available_fields],
            "additionalProperties": False,
        },
    }


def _call_field_match_model(client: OpenAI, model: str, raw_specifications: list, description: str,
                             available_fields: list[dict], use_strict_schema: bool, extra_kwargs: dict | None = None):
    """Prompts a model to fill form fields from raw specs using a strict schema or an example fallback."""
    fields_text = "\n".join(
        f"- {f['id']} (\"{f['label']}\"): " + (f"choose one of {f['options']}" if f["type"] == "select" else "free text")
        for f in available_fields
    )
    prompt = (
        "You are filling out a car listing form. You have the car's actual specifications "
        "(from its spec sheet, as verbatim label/value pairs) and a list of fields on the "
        "form that need values. For EACH field below, decide the right value from the car's "
        "specifications — sheets word things differently than form field labels (abbreviations, "
        "different units, translated engineering codes) so match by MEANING, using your "
        "knowledge of automotive terminology, not exact wording. For a field with a fixed list "
        "of choices, you MUST pick one of the exact listed choices, or null if none genuinely "
        "fits. For a free-text field, write the appropriate value (e.g. convert units if the "
        "field expects a different one than the sheet uses). Never invent a value the car's "
        "specifications don't support — return null for anything you can't determine.\n\n"
        f"Car's specifications (verbatim from its spec sheet):\n{json.dumps(raw_specifications, indent=2)}\n\n"
        f"Car's description:\n{description}\n\n"
        f"Form fields to fill:\n{fields_text}\n"
    )
    kwargs = {
        "model": model,
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": prompt}],
        **(extra_kwargs or {}),
    }
    if use_strict_schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": build_field_match_schema(available_fields),
        }
    else:
        example = {f["id"]: (f["options"][0] if f["type"] == "select" else "example value") for f in available_fields[:3]}
        kwargs["messages"][0]["content"] += (
            "\n\nRespond with ONLY a single JSON object with one key per field id above (the "
            "example below shows the shape for the first few fields only — include ALL of "
            "them in your real answer, using null for any you can't determine):\n"
            f"{json.dumps(example, indent=2)}\nNo markdown fences, no prose before or after."
        )
    attempt = "strict" if use_strict_schema else "fallback"
    return _create_completion(client, kwargs, log_context=f"match_fields[{model}, {attempt}]")


def _match_fields_single_provider(client: OpenAI, model: str, raw_specifications: list, description: str,
                                   available_fields: list[dict], extra_kwargs: dict | None = None) -> dict:
    """Runs strict then fallback field matching on one provider and raises RuntimeError if both fail."""
    valid_values = {
        f["id"]: (set(f["options"]) if f["type"] == "select" else None) for f in available_fields
    }

    def _try(use_strict_schema: bool):
        """Runs one field-matching attempt and returns non-empty values valid for their fields, or an error."""
        content, finish_reason, refusal = _call_field_match_model(
            client, model, raw_specifications, description, available_fields, use_strict_schema, extra_kwargs)
        if not content or not content.strip():
            return None, f"empty content (finish_reason={finish_reason!r}, refusal={refusal!r})"
        try:
            parsed = json.loads(_strip_markdown_fences(content))
        except json.JSONDecodeError as e:
            return None, f"invalid JSON ({e}): {content[:300]!r}"
        if not isinstance(parsed, dict):
            return None, f"expected a JSON object, got {type(parsed).__name__}"
        cleaned = {}
        for field_id, value in parsed.items():
            if field_id not in valid_values or value is None or not isinstance(value, str) or not value.strip():
                continue
            allowed = valid_values[field_id]
            if allowed is not None:
                canonical = _canonical_option(value, allowed)
                if canonical is None:
                    continue
                value = canonical
            cleaned[field_id] = value
        return cleaned, None

    parsed, error1 = _try(use_strict_schema=True)
    if parsed is None:
        parsed, error2 = _try(use_strict_schema=False)
        if parsed is None:
            raise RuntimeError(f"Attempt 1 (strict schema): {error1}\nAttempt 2 (fallback): {error2}")
    return parsed


@observe()
def match_fields_with_llm(raw_specifications: list, description: str, available_fields: list[dict]) -> dict:
    """Maps raw specs onto form fields using parallel providers, earlier providers winning each field."""
    if not available_fields:
        return {}

    providers = _match_providers()

    results, errors = {}, {}
    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        futures = {
            _submit_with_context(pool, _match_fields_single_provider, client, model, raw_specifications, description,
                                 available_fields, extra_kwargs): name
            for name, client, model, extra_kwargs in providers
        }
        for future, name in futures.items():
            try:
                results[name] = future.result()
                logger.info("match_fields[%s]: %d field(s) filled", name, len(results[name]))
            except Exception as e:
                errors[name] = str(e)
                logger.warning("match_fields[%s]: provider failed: %s", name, e)

    if not results:
        detail = "\n".join(f"{name}: {err}" for name, err in errors.items())
        raise RuntimeError(f"Field matching failed on all providers.\n{detail}")
    if errors:
        logger.warning("match_fields: degraded — %d/%d provider(s) failed (%s), "
                        "returning results from the rest", len(errors), len(providers), ", ".join(errors))

    merged = {}
    for name, _, _, _ in providers:
        for field_id, value in results.get(name, {}).items():
            merged.setdefault(field_id, value)
    return merged


def _enum_safe(values: list) -> list:
    """Returns the stripped, deduplicated, non-empty string values usable as a strict-schema enum."""
    seen, out = set(), []
    for v in values:
        if not isinstance(v, str):
            continue
        s = v.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def build_match_all_schema(available_fields: list[dict], available_features: list,
                           raw_features: list) -> dict:
    """Returns the strict schema nesting field values and feature-matching arrays in one combined object."""
    field_properties = {}
    for f in available_fields:
        if f["type"] == "select":
            field_properties[f["id"]] = {
                "anyOf": [{"type": "string", "enum": f["options"]}, {"type": "null"}]}
        else:
            field_properties[f["id"]] = {"type": ["string", "null"]}

    return {
        "name": "matched_fields_and_features",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "object",
                    "properties": field_properties,
                    "required": [f["id"] for f in available_fields],
                    "additionalProperties": False,
                },
                "matched_features": {
                    "type": "array", "items": {"type": "string", "enum": _enum_safe(available_features)}},
                "important_unmatched_features": {
                    "type": "array", "items": {"type": "string", "enum": _enum_safe(raw_features)}},
                "other_unmatched_features": {
                    "type": "array", "items": {"type": "string", "enum": _enum_safe(raw_features)}},
            },
            "required": ["fields", "matched_features",
                         "important_unmatched_features", "other_unmatched_features"],
            "additionalProperties": False,
        },
    }


def _call_match_all_model(client: OpenAI, model: str, raw_specifications: list,
                          raw_features: list, description: str,
                          available_fields: list[dict], available_features: list,
                          use_strict_schema: bool, extra_kwargs: dict | None = None):
    """Prompts a model to fill fields and match checkboxes in one call, strict schema or example fallback."""
    fields_text = "\n".join(
        f"- {f['id']} (\"{f['label']}\"): "
        + (f"choose one of {f['options']}" if f["type"] == "select" else "free text")
        for f in available_fields
    )
    vocab_text = ", ".join(available_features)

    prompt = (
        "You are filling out a car listing form from the car's actual specifications "
        "(from its spec sheet, as verbatim label/value pairs). There are TWO separate "
        "jobs below. Do both, and answer both in one JSON object.\n\n"

        f"Car's specifications (verbatim from its spec sheet):\n"
        f"{json.dumps(raw_specifications, indent=2)}\n\n"
        f"Car's features (verbatim from its spec sheet):\n"
        f"{json.dumps(raw_features, indent=2)}\n\n"
        f"Car's description:\n{description}\n\n"

        "=== JOB 1: the form's value fields ===\n"
        "For EACH field below, decide the right value from the car's specifications — "
        "sheets word things differently than form field labels (abbreviations, different "
        "units, translated engineering codes) so match by MEANING, using your knowledge of "
        "automotive terminology, not exact wording. For a field with a fixed list of "
        "choices, you MUST pick one of the exact listed choices, or null if none genuinely "
        "fits. For a free-text field, write the appropriate value (e.g. convert units if the "
        "field expects a different one than the sheet uses). Never invent a value the car's "
        "specifications don't support — return null for anything you can't determine.\n\n"
        f"Form fields to fill:\n{fields_text}\n\n"

        "=== JOB 2: the form's checkboxes ===\n"
        "Choose ONLY the options from the allowed list below that this car genuinely has. "
        "Sheets word things differently from our checkboxes — abbreviations, translated "
        "engineering codes, completely different phrasing for the same physical feature "
        "(e.g. a sheet's \"10.25-inch smart display\" IS a \"Touch Screen\"; \"PUSH STARTING "
        "SYSTEM\" IS \"Push Start\"; \"LED Headlamps\" IS \"LED headlights\"; a \"Dual Auto "
        "Air Conditioner\" IS \"Air conditioning\"). Match by MEANING, using your knowledge "
        "of automotive terminology, not exact wording. Never include an option the car "
        "doesn't actually have, and never invent an option outside the allowed list — when "
        "genuinely unsure, leave it out.\n\n"
        "Some of the car's real features genuinely have NO corresponding checkbox in the "
        "allowed list at all. List those, VERBATIM as they appear in the car's features "
        "above, split into two tiers so a human reviewer isn't overwhelmed: "
        "\"important_unmatched_features\" for things a car buyer would actually care about "
        "and a listing should call out (e.g. a sunroof, premium sound system, ventilated "
        "seats, a notable driver-assist feature), and \"other_unmatched_features\" for "
        "everything else real but minor or generic (routine trim badges, standard safety "
        "mentions, small convenience details). Only include genuinely real, present features "
        "in either list, not things that just didn't fit well — when unsure whether something "
        "is important or minor, put it in other_unmatched_features.\n\n"
        f"Allowed checkbox options (choose ONLY from these, matching spelling exactly):\n"
        f"{vocab_text}\n"
    )

    kwargs = {
        "model": model,
        "max_tokens": 7000,
        "messages": [{"role": "user", "content": prompt}],
        **(extra_kwargs or {}),
    }
    if use_strict_schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": build_match_all_schema(
                available_fields, available_features, raw_features),
        }
    else:
        example = {
            "fields": {f["id"]: (f["options"][0] if f["type"] == "select" else "example value")
                       for f in available_fields[:3]},
            "matched_features": available_features[:2],
            "important_unmatched_features": [],
            "other_unmatched_features": [],
        }
        kwargs["messages"][0]["content"] += (
            "\n\nRespond with ONLY a single JSON object of this shape (the \"fields\" example "
            "shows the first few field ids only — include ALL of them in your real answer, "
            "using null for any you can't determine):\n"
            f"{json.dumps(example, indent=2)}\nNo markdown fences, no prose before or after."
        )
    attempt = "strict" if use_strict_schema else "fallback"
    return _create_completion(client, kwargs, log_context=f"match_all[{model}, {attempt}]")


def _parse_match_all(content: str, available_fields: list[dict],
                     available_features: list) -> dict | None:
    """Parses the combined answer, dropping unknown fields, invalid select options and unoffered checkbox matches."""
    parsed = json.loads(_strip_markdown_fences(content))
    if not isinstance(parsed, dict):
        return None

    by_id = {f["id"]: f for f in available_fields}
    fields_out = {}
    for field_id, value in (parsed.get("fields") or {}).items():
        spec = by_id.get(field_id)
        if spec is None or value is None or value == "":
            continue
        if spec["type"] == "select":
            canonical = _canonical_option(str(value), spec["options"])
            if canonical is None:
                logger.info("match_all: %r is not one of %s's options — left blank "
                            "rather than guessed", value, field_id)
                continue
            fields_out[field_id] = canonical
        else:
            fields_out[field_id] = str(value)

    allowed = set(available_features)
    matched = [f for f in (parsed.get("matched_features") or []) if f in allowed]

    return {
        "fields": fields_out,
        "features": {
            "matched_features": matched,
            "important_unmatched_features": list(
                parsed.get("important_unmatched_features") or []),
            "other_unmatched_features": list(
                parsed.get("other_unmatched_features") or []),
        },
    }


def match_all_with_llm(raw_specifications: list, raw_features: list, description: str,
                       available_fields: list[dict], available_features: list) -> dict:
    """Fills fields and matches checkboxes with the first configured provider, after a token-overlap feature pass."""
    if not available_fields and not available_features:
        return {"fields": {}, "matched_features": [], "suggested_features": []}

    deterministic = {}
    for raw_item in raw_features:
        if not isinstance(raw_item, str) or not raw_item.strip():
            continue
        label = _best_flat_vocab_match(raw_item, available_features,
                                       config.FEATURE_MATCH_DECISIVE_THRESHOLD)
        if label:
            deterministic[raw_item] = label
    leftovers = [f for f in raw_features if f not in deterministic]
    if deterministic:
        logger.info("match_all: %d/%d feature(s) matched deterministically by token "
                    "overlap, %d left for the model", len(deterministic),
                    len(raw_features), len(leftovers))

    providers = _match_providers()
    name, client, model, extra_kwargs = providers[0]
    if hasattr(client, 'with_options'):
        client = client.with_options(timeout=config.MATCH_ALL_TIMEOUT_SECONDS)
    if len(providers) > 1:
        logger.info("match_all: %d provider(s) configured, using %s only — one question, "
                    "one answer (see match_all_with_llm)", len(providers), name)

    def _try(use_strict_schema: bool):
        """Runs one combined matching attempt, repairing a reply cut off at the token cap, and returns the result or an error."""
        content, finish_reason, refusal = _call_match_all_model(
            client, model, raw_specifications, leftovers, description,
            available_fields, available_features, use_strict_schema, extra_kwargs)
        if refusal:
            return None, f"model refused: {refusal}"
        if not content:
            return None, f"empty content (finish_reason={finish_reason})"
        if finish_reason == "length":
            recovered, _dropped = recover_truncated_json(_strip_markdown_fences(content))
            if recovered is not None:
                content = json.dumps(recovered)
        try:
            result = _parse_match_all(content, available_fields, available_features)
        except json.JSONDecodeError as e:
            return None, f"unparseable JSON: {e}"
        if result is None:
            return None, "wrong shape — not a JSON object"
        return result, None

    strict_skipped = config.use_claude_cli_for_match()
    if strict_skipped:
        result, error1 = None, ("not attempted — this backend has no "
                                "structured-output mode")
    else:
        result, error1 = _try(use_strict_schema=True)

    if result is None:
        result, error2 = _try(use_strict_schema=False)
        if result is None:
            raise RuntimeError(
                f"Combined matching failed on {name}.\n"
                f"Attempt 1 (strict schema): {error1}\n"
                f"Attempt 2 (fallback): {error2}")

    matched = list(dict.fromkeys(
        list(deterministic.values()) + result["features"]["matched_features"]))

    suggested = ([{"label": f, "important": True}
                  for f in result["features"]["important_unmatched_features"]]
                 + [{"label": f, "important": False}
                    for f in result["features"]["other_unmatched_features"]])

    logger.info("match_all[%s]: %d field(s) filled, %d checkbox(es) matched "
                "(%d deterministic + %d from the model), %d suggestion(s)",
                name, len(result["fields"]), len(matched), len(deterministic),
                len(result["features"]["matched_features"]), len(suggested))
    return {"fields": result["fields"], "matched_features": matched,
            "suggested_features": suggested}
def _call_model(client: OpenAI, image_content: list, use_strict_schema: bool, context_text: str = "",
                 model: str | None = None):
    """Requests extraction from the model with a strict schema or example-shaped fallback prompt."""
    model = model or MODEL
    kwargs = {
        "model": model,
        "max_tokens": config.EXTRACTION_MAX_TOKENS,
        "messages": [{
            "role": "user",
            "content": [{"type": "text", "text": PROMPT + context_text}, *image_content],
        }],
    }
    if use_strict_schema:
        kwargs["response_format"] = {"type": "json_schema", "json_schema": build_response_schema()}
    else:
        kwargs["messages"][0]["content"][0]["text"] += (
            "\n\nRespond with ONLY a single JSON object in EXACTLY this shape "
            "(these are example values to show the structure — use real "
            "values from the document, not these examples verbatim). No "
            "markdown fences, no prose before or after:\n"
            f"{json.dumps(_build_example_output(), indent=2)}"
        )

    attempt = "strict" if use_strict_schema else "fallback"
    return _create_completion(client, kwargs, log_context=f"extract_from_pdf[{model}, {attempt}]")


def _build_ground_truth_context(context: dict | None) -> str:
    """Builds prompt text listing known car details to anchor ambiguous reads, or an empty string."""
    if not context:
        return ""
    parts = [f"{k.replace('_', ' ')}: {v}" for k, v in context.items() if v]
    if not parts:
        return ""
    return (
        "\n\nGround truth for the car in these images (from Salesforce/the Model "
        "Code convention) — use this to resolve ambiguous reads (e.g. a color under "
        "studio lighting, a hard-to-read trim badge), not as a substitute for what's "
        "actually on the sheet:\n" + "\n".join(f"- {p}" for p in parts)
    )


def _cli_client(model: str | None = None):
    """Builds a ClaudeCLIClient with the configured timeout and the given or default CLI model."""
    from app.claude_cli_client import ClaudeCLIClient
    return ClaudeCLIClient(timeout=config.CLI_TIMEOUT_SECONDS,
                           model=model or config.CLI_MODEL)


def _match_providers() -> list[tuple[str, OpenAI, str, dict]]:
    """Returns (name, client, model, extra_kwargs) matching providers chosen from config, raising if none."""
    if config.use_claude_cli_for_match():
        logger.info("match: local Claude Code CLI (%s) — single provider, no schema "
                    "enforcement, post-validation still applies", config.MATCH_CLI_MODEL)
        return [("Claude CLI", _cli_client(config.MATCH_CLI_MODEL), config.MATCH_CLI_MODEL, {})]
    if config.use_edenai_for_match():
        client = OpenAI(base_url=config.EDENAI_BASE_URL, api_key=config.edenai_key(),
                        timeout=config.MATCH_TIMEOUT_SECONDS,
                        max_retries=config.MATCH_MAX_RETRIES)
        logger.info('match: Eden AI (%s) — single provider', config.EDENAI_MATCH_MODEL)
        return [('Eden AI', client, config.EDENAI_MATCH_MODEL, {})]
    if config.use_anthropic_for_match():
        client = OpenAI(base_url=config.ANTHROPIC_BASE_URL,
                        api_key=os.environ["ANTHROPIC_API_KEY"],
                        timeout=config.MATCH_TIMEOUT_SECONDS, max_retries=config.MATCH_MAX_RETRIES)
        logger.info("match: Anthropic/Claude (%s) — single provider", config.MATCH_ANTHROPIC_MODEL)
        return [("Anthropic/Claude", client, config.MATCH_ANTHROPIC_MODEL, {})]

    providers = []
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        groq_client = OpenAI(base_url=GROQ_BASE_URL, api_key=groq_key,
                              timeout=config.MATCH_TIMEOUT_SECONDS, max_retries=config.MATCH_MAX_RETRIES)
        providers.append((
            "Groq/gpt-oss", groq_client, GROQ_MODEL,
            {"reasoning_effort": "low"},
        ))
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_key:
        or_client = OpenAI(base_url=config.MATCH_BASE_URL, api_key=openrouter_key,
                            timeout=config.MATCH_TIMEOUT_SECONDS, max_retries=config.MATCH_MAX_RETRIES)
        providers.append(("OpenRouter", or_client, config.MATCH_MODEL, {}))

    if not providers:
        raise RuntimeError(
            "No LLM provider configured. Set GROQ_API_KEY and/or OPENROUTER_API_KEY, "
            "or set LLM_PROVIDER=anthropic with ANTHROPIC_API_KEY."
        )
    return providers


def small_text_completion(messages: list, response_format: dict, max_tokens: int,
                          log_context: str):
    """Runs a logged zero-temperature text completion on the first configured matching provider."""
    name, client, model, extra = _match_providers()[0]
    return _create_completion(
        client,
        {"model": model, "messages": messages, "response_format": response_format,
         "max_tokens": max_tokens, "temperature": 0, **(extra or {})},
        log_context=f"{log_context}[{name}/{model}]",
    )


def _extraction_client_and_model() -> tuple[OpenAI, str]:
    """Returns the client and model for extraction: Claude CLI, Anthropic, Eden AI or OpenRouter per config."""
    if config.use_claude_cli():
        logger.info("extract_from_pdf: local Claude Code CLI (%s)", config.CLI_MODEL)
        return (_cli_client(), config.CLI_MODEL)
    if config.use_anthropic():
        return (
            OpenAI(base_url=config.ANTHROPIC_BASE_URL, api_key=os.environ["ANTHROPIC_API_KEY"],
                   timeout=config.EXTRACTION_TIMEOUT_SECONDS, max_retries=config.EXTRACTION_MAX_RETRIES),
            config.ANTHROPIC_MODEL,
        )
    if config.use_edenai():
        logger.info('extract_from_pdf: Eden AI (%s)', config.EDENAI_MODEL)
        return (
            OpenAI(base_url=config.EDENAI_BASE_URL, api_key=config.edenai_key(),
                   timeout=config.EXTRACTION_TIMEOUT_SECONDS,
                   max_retries=config.EXTRACTION_MAX_RETRIES),
            config.EDENAI_MODEL,
        )
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return (
        OpenAI(base_url=BASE_URL, api_key=api_key,
               timeout=config.EXTRACTION_TIMEOUT_SECONDS, max_retries=config.EXTRACTION_MAX_RETRIES),
        MODEL,
    )


def _shape_issues(parsed: dict) -> list[str]:
    """Returns a list of problems with the parsed extraction: missing keys, non-list arrays, or empty specs."""
    issues = []
    for key in ("brand", "model", "trim", "year", "raw_specifications", "description", "raw_features"):
        if key not in parsed:
            issues.append(f"missing top-level key '{key}'")
    if "raw_specifications" in parsed and not isinstance(parsed["raw_specifications"], list):
        issues.append("'raw_specifications' should be an array, got " + type(parsed["raw_specifications"]).__name__)
    if "raw_features" in parsed and not isinstance(parsed["raw_features"], list):
        issues.append("'raw_features' should be an array, got " + type(parsed["raw_features"]).__name__)

    if isinstance(parsed.get("raw_specifications"), list) and not parsed["raw_specifications"]:
        issues.append("'raw_specifications' is empty — the model returned a skeleton, not an extraction")
    return issues


def order_pdf_candidates(pdfs: list[dict], search_key: str) -> list[dict]:
    """Orders PDFs with the exact '<search_key>.pdf' name first, followed by the rest in original order."""
    target = f"{search_key.strip().lower()}.pdf"
    exact = [p for p in pdfs if p["name"].strip().lower() == target]
    rest = [p for p in pdfs if p not in exact]
    return exact + rest


def extract_from_pdfs(pdfs: list[dict], search_key: str, context: dict | None = None) -> dict:
    """Extracts from each candidate PDF in turn until one succeeds, stopping early on truncation errors."""
    if not pdfs:
        raise RuntimeError("no spec-sheet PDF in the folder to extract from")

    candidates = order_pdf_candidates(pdfs, search_key)
    errors = []
    for i, pdf in enumerate(candidates):
        try:
            result = extract_from_pdf(pdf["path"], context)
        except RuntimeError as e:
            errors.append(f"{pdf['name']}: {e}")

            if "TRUNCATED" in str(e):
                logger.warning(
                    "extract_from_pdfs: %s hit the output budget — not trying the "
                    "remaining %d PDF(s), which describe the same car and would fail "
                    "the same way.",
                    pdf["name"], len(candidates) - i - 1,
                )
                break

            remaining = len(candidates) - i - 1
            logger.warning("extract_from_pdfs: %s yielded nothing usable%s", pdf["name"],
                           f", trying {remaining} other PDF(s) in the folder" if remaining else "")
            continue
        if i > 0:
            logger.info("extract_from_pdfs: recovered using %s after %d unusable PDF(s)", pdf["name"], i)
        return result

    raise RuntimeError(
        f"None of the {len(candidates)} PDF(s) in the folder produced a usable extraction. "
        + " | ".join(errors)
    )


@observe()
def extract_from_pdf(pdf_path: str, context: dict | None = None) -> dict:
    """Extracts listing data from a PDF via text layer or page images, with retries and feature grouping."""
    t0 = time.monotonic()
    client, model = _extraction_client_and_model()

    logger.info("extract_from_pdf: starting %s", pdf_path)

    text_layer = extract_text_layer(pdf_path)
    if text_layer:
        source = "text layer"
        document_content = [{
            "type": "text",
            "text": ("Below is the spec sheet's own text, extracted directly from the PDF "
                     "label by label, in the sheet's own order.\n\n" + text_layer),
        }]
    else:
        source = "page images"
        page_images_b64 = pdf_to_page_images_b64(pdf_path)
        logger.info("extract_from_pdf: rendered %d page image(s)", len(page_images_b64))
        document_content = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}
            for img in page_images_b64
        ]
    logger.info("extract_from_pdf: reading %s via the %s", pdf_path, source)
    context_text = _build_ground_truth_context(context)

    def _try(use_strict_schema: bool):
        """Runs one extraction attempt, repairing truncated JSON if enabled, and returns parsed data or an error."""
        content, finish_reason, refusal = _call_model(client, document_content, use_strict_schema,
                                                       context_text, model)
        if not content or not content.strip():
            return None, f"empty content (finish_reason={finish_reason!r}, refusal={refusal!r})"

        truncated = finish_reason in ("length", "max_tokens")

        try:
            parsed = json.loads(_strip_markdown_fences(content))
        except json.JSONDecodeError as e:
            if not truncated:
                return None, f"invalid JSON ({e}): {content[:300]!r}"

            if not config.EXTRACTION_RECOVER_TRUNCATED:
                return None, (
                    f"output TRUNCATED at the {config.EXTRACTION_MAX_TOKENS}-token cap "
                    "— this sheet is denser than the budget allows. Retrying unchanged "
                    "will fail identically."
                )

            parsed, dropped = recover_truncated_json(_strip_markdown_fences(content))
            if parsed is None:
                return None, (
                    f"output TRUNCATED at the {config.EXTRACTION_MAX_TOKENS}-token cap "
                    "and could not be repaired."
                )
            logger.warning(
                "extract_from_pdf: recovered a TRUNCATED extraction — repaired the JSON "
                "and kept %d spec row(s); ~%d trailing characters were lost.",
                len(parsed.get("raw_specifications") or []), dropped,
            )
            notes = parsed.setdefault("low_confidence_fields", [])
            if isinstance(notes, list):
                notes.append(
                    "TRUNCATED: the spec sheet was longer than the model's output budget. "
                    "Later specification rows and features are missing — verify against "
                    "the sheet before publishing."
                )

        issues = _shape_issues(parsed)
        if issues:
            return None, f"wrong shape — {'; '.join(issues)}. Got: {json.dumps(parsed)[:300]}"
        return parsed, None

    strict_skipped = config.use_claude_cli()
    if strict_skipped:
        parsed, error1 = None, ('not attempted — the local CLI has no '
                                'structured-output mode, so this would have '
                                'been an identical request to attempt 2')
    else:
        parsed, error1 = _try(use_strict_schema=True)

    used_fallback = False
    fallback_errors = []
    if parsed is None:
        used_fallback = True
        for attempt in range(config.EXTRACTION_FALLBACK_ATTEMPTS):
            parsed, err = _try(use_strict_schema=False)
            if parsed is not None:
                break
            fallback_errors.append(f"Attempt {attempt + 2} (example-based fallback): {err}")
            logger.warning("extract_from_pdf: fallback attempt %d/%d failed — %s",
                           attempt + 1, config.EXTRACTION_FALLBACK_ATTEMPTS, err)

    if parsed is None:
        logger.error("extract_from_pdf: all %d attempts failed after %.2fs (%s)",
                     (0 if strict_skipped else 1) + len(fallback_errors), time.monotonic() - t0, pdf_path)
        raise RuntimeError(
            "Every extraction attempt failed to produce a usable result.\n"
            + f"Attempt 1 (strict schema): {error1}\n"
            + "\n".join(fallback_errors)
            + "\nIf the failures above say 'raw_specifications is empty', the model "
            "returned a correctly-shaped but contentless skeleton — that's provider "
            "flakiness on the free tier rather than a prompt problem, and retrying "
            "usually works. Upload the spec sheet manually to continue now."
        )

    if used_fallback and not strict_skipped:
        parsed.setdefault("low_confidence_fields", []).append(
            "__extraction_used_fallback_mode__ (strict schema attempt failed shape validation)"
        )
    logger.info("extract_from_pdf: succeeded in %.2fs (fallback=%s)", time.monotonic() - t0, used_fallback)
    return _enrich_features_from_raw(parsed)
