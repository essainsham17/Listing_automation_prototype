"""Resolves extracted vehicle, feature and specification text onto admin taxonomy catalog ids."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)

CONFIDENT_THRESHOLD = 0.82
CANDIDATE_FLOOR = 0.35
MAX_CANDIDATES = 5


@dataclass
class TaxonomyEntry:
    """Selectable taxonomy row with id, name, aliases and child entries for the next level down."""

    id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    children: list["TaxonomyEntry"] = field(default_factory=list)

    def all_names(self) -> list[str]:
        """Returns the entry's name followed by its aliases."""
        return [self.name, *self.aliases]


@dataclass
class Resolution:
    """Outcome for one field: status, matched id and name, confidence, method and ranked candidates."""

    field_name: str
    extracted: str
    status: str
    id: str | None = None
    name: str | None = None
    confidence: float = 0.0
    method: str = ""
    candidates: list[dict] = field(default_factory=list)

    @property
    def needs_human(self) -> bool:
        """Returns whether the status is ambiguous or missing and so needs a reviewer."""
        return self.status in ("ambiguous", "missing")

    def as_dict(self) -> dict:
        """Returns the resolution as a plain dict with confidence rounded to three decimals."""
        return {
            "field": self.field_name,
            "extracted": self.extracted,
            "status": self.status,
            "id": self.id,
            "name": self.name,
            "confidence": round(self.confidence, 3),
            "method": self.method,
            "candidates": self.candidates,
        }


def _normalize(text: str) -> str:
    """Lowercases text and strips every character that is not a letter or digit."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _token_set(text: str) -> set[str]:
    """Returns the set of lowercased tokens, turning punctuation into spaces but keeping dots next to digits."""
    cleaned = re.sub(r"(?<!\d)\.(?!\d)|[^\w.\s]", " ", (text or "").lower())
    return set(cleaned.split())


def _score(candidate: str, target: str) -> float:
    """Returns token similarity in [0, 1] as the larger of Jaccard and discounted containment."""
    c_tokens, t_tokens = _token_set(candidate), _token_set(target)
    if not c_tokens or not t_tokens:
        return 0.0
    overlap = c_tokens & t_tokens
    if not overlap:
        return 0.0
    jaccard = len(overlap) / len(c_tokens | t_tokens)
    containment = len(overlap) / min(len(c_tokens), len(t_tokens))
    return max(jaccard, containment * 0.85)


def resolve_one(
    extracted: str,
    options: Iterable[TaxonomyEntry],
    field_name: str,
) -> Resolution:
    """Resolves a value by exact, normalized, then token match on options, else ambiguous, missing or absent."""
    options = list(options)
    extracted = (extracted or "").strip()

    if not extracted:
        return Resolution(field_name, "", "absent")

    if not options:
        return Resolution(field_name, extracted, "missing")

    for entry in options:
        if extracted in entry.all_names():
            return Resolution(field_name, extracted, "matched", entry.id, entry.name, 1.0, "exact")

    target_norm = _normalize(extracted)
    for entry in options:
        if any(_normalize(n) == target_norm for n in entry.all_names()):
            return Resolution(
                field_name, extracted, "matched", entry.id, entry.name, 0.98, "normalized"
            )

    scored = sorted(
        ((entry, _score(entry.name, extracted)) for entry in options),
        key=lambda pair: pair[1],
        reverse=True,
    )

    top_entry, top_score = scored[0]
    runner_up = scored[1][1] if len(scored) > 1 else 0.0

    candidates = [
        {"id": e.id, "name": e.name, "score": round(s, 3)}
        for e, s in scored[:MAX_CANDIDATES]
        if s >= CANDIDATE_FLOOR
    ]

    if top_score >= CONFIDENT_THRESHOLD and (top_score - runner_up) >= 0.10:
        return Resolution(
            field_name, extracted, "matched", top_entry.id, top_entry.name, top_score, "token"
        )

    if candidates:
        return Resolution(
            field_name, extracted, "ambiguous", confidence=top_score, method="token",
            candidates=candidates,
        )

    logger.info(
        "taxonomy: no candidate for %s=%r among %d option(s) — needs a new entry",
        field_name, extracted, len(options),
    )
    return Resolution(field_name, extracted, "missing", confidence=top_score, method="token")


def resolve_vehicle(extracted: dict, brands: list[TaxonomyEntry]) -> dict:
    """Resolves brand, then model within that brand, then trim within that model, reporting where it blocked."""
    brand_res = resolve_one(extracted.get("brand", ""), brands, "brand")

    if brand_res.status != "matched":
        blocked = _blocked("model", extracted.get("model", ""), "brand")
        return {
            "brand": brand_res.as_dict(),
            "model": blocked.as_dict(),
            "trim": _blocked("trim", extracted.get("trim", ""), "brand").as_dict(),
            "ready_to_prefill": False,
            "blocked_on": "brand",
        }

    brand_entry = next(b for b in brands if b.id == brand_res.id)
    model_res = resolve_one(extracted.get("model", ""), brand_entry.children, "model")

    if model_res.status != "matched":
        return {
            "brand": brand_res.as_dict(),
            "model": model_res.as_dict(),
            "trim": _blocked("trim", extracted.get("trim", ""), "model").as_dict(),
            "ready_to_prefill": False,
            "blocked_on": "model",
        }

    model_entry = next(m for m in brand_entry.children if m.id == model_res.id)
    trim_res = resolve_one(extracted.get("trim", ""), model_entry.children, "trim")

    return {
        "brand": brand_res.as_dict(),
        "model": model_res.as_dict(),
        "trim": trim_res.as_dict(),
        "ready_to_prefill": trim_res.status == "matched",
        "blocked_on": None if trim_res.status == "matched" else "trim",
    }


def _blocked(field_name: str, extracted: str, parent: str) -> Resolution:
    """Builds a missing Resolution marking a level skipped because its parent did not resolve."""
    return Resolution(
        field_name, extracted, "missing", method=f"blocked_on_{parent}"
    )


@dataclass
class FeatureEntry:
    """Catalog feature or specification value row with id, name, slug, parent name and parent id."""
    id: str
    name: str
    slug: str = ""
    parent: str = ""
    parent_id: str = ""

    def all_names(self) -> list[str]:
        """Returns the non-empty name and slug of the entry."""
        return [n for n in (self.name, self.slug) if n]


@dataclass
class FeatureCatalog:
    """Feature catalog holding all feature entries and the names of mandatory parent groups."""
    features: list[FeatureEntry] = field(default_factory=list)
    mandatory_groups: list[str] = field(default_factory=list)

    def by_group(self, group: str) -> list[FeatureEntry]:
        """Returns the features belonging to the given parent group."""
        return [f for f in self.features if f.parent == group]


def parse_feature_catalog(payload: dict) -> FeatureCatalog:
    """Builds a FeatureCatalog from parent_features and features lists, recording mandatory groups."""
    parents = {}
    mandatory = []
    for group in payload.get("parent_features") or []:
        name = str(group.get("name") or group.get("label") or "")
        parents[name] = str(group.get("id") or "")
        if group.get("mandatory"):
            mandatory.append(name)

    features = []
    for row in payload.get("features") or []:
        parent = str(row.get("parent") or row.get("parent_feature") or "")
        features.append(FeatureEntry(
            id=str(row.get("id") or ""),
            name=str(row.get("name") or row.get("label") or ""),
            slug=str(row.get("slug") or ""),
            parent=parent,
            parent_id=parents.get(parent, ""),
        ))
    return FeatureCatalog(features=features, mandatory_groups=mandatory)


def resolve_features(labels: Iterable[str], catalog: FeatureCatalog) -> dict:
    """Maps labels to unique catalog feature ids and reports unresolved labels and unfilled mandatory groups."""
    seen_ids: set[str] = set()
    matched: list[dict] = []
    unresolved: list[dict] = []

    for label in labels:
        label = (label or "").strip()
        if not label:
            continue

        resolution = resolve_one(label, catalog.features, "feature")
        if resolution.status == "matched":
            if resolution.id in seen_ids:
                continue
            seen_ids.add(resolution.id)
            entry = next(f for f in catalog.features if f.id == resolution.id)
            matched.append({
                "id": entry.id,
                "name": entry.name,
                "slug": entry.slug,
                "parent": entry.parent,
                "parent_id": entry.parent_id,
                "matched_from": label,
                "method": resolution.method,
            })
        else:
            unresolved.append({
                "label": label,
                "status": resolution.status,
                "candidates": resolution.candidates,
            })

    filled_groups = {m["parent"] for m in matched}
    missing_mandatory = [g for g in catalog.mandatory_groups if g not in filled_groups]

    if missing_mandatory:
        logger.info(
            "taxonomy: no feature resolved into mandatory group(s) %s — the form will "
            "block submission until a human ticks something there",
            missing_mandatory,
        )

    return {
        "features": matched,
        "feature_ids": [m["id"] for m in matched],
        "unresolved": unresolved,
        "mandatory_groups_unfilled": missing_mandatory,
        "ready_to_prefill": not missing_mandatory,
    }


_NUMERIC_PARENTS = {"Seats", "Cylinders", "Doors", "Wheel Size"}

_LABEL_HINTS = {
    "transmission": "Transmission", "gearbox": "Transmission",
    "fuel": "Fuel Type", "fuel type": "Fuel Type", "engine type": "Fuel Type",
    "drive": "Drive Type", "drive type": "Drive Type", "drivetrain": "Drive Type",
    "seats": "Seats", "seating": "Seats", "seating capacity": "Seats", "no of seats": "Seats",
    "doors": "Doors", "no of doors": "Doors",
    "cylinders": "Cylinders", "no of cylinders": "Cylinders", "engine cylinders": "Cylinders",
    "body": "Body Type", "body type": "Body Type", "body style": "Body Type",
    "colour": "Color", "color": "Color", "exterior color": "Color",
    "exterior colour": "Color", "body color": "Color", "paint": "Color",
    "interior color": "Interior Color", "interior colour": "Interior Color",
    "upholstery": "Interior Color", "trim color": "Interior Color",
    "steering": "Steering Side", "steering side": "Steering Side", "steering position": "Steering Side",
    "wheel size": "Wheel Size", "wheels": "Wheel Size", "rim size": "Wheel Size",
    "tyre size": "Wheel Size", "tire size": "Wheel Size",
    "regional specification": "Regional Specification", "region": "Regional Specification",
    "specs": "Regional Specification", "regional specs": "Regional Specification",
}


@dataclass
class SpecCatalog:
    """Specification catalog holding value entries, parent names and mandatory parent names."""
    values: list[FeatureEntry] = field(default_factory=list)
    parents: list[str] = field(default_factory=list)
    mandatory_parents: list[str] = field(default_factory=list)

    def for_parent(self, parent: str) -> list[FeatureEntry]:
        """Returns the specification values belonging to the given parent."""
        return [v for v in self.values if v.parent == parent]


def parse_spec_catalog(payload: dict) -> SpecCatalog:
    """Builds a SpecCatalog from parent_specifications and values, skipping non-published rows."""
    parents, mandatory = [], []
    parent_ids = {}
    for group in payload.get("parent_specifications") or []:
        name = str(group.get("name") or group.get("label") or "")
        parents.append(name)
        parent_ids[name] = str(group.get("id") or "")
        if group.get("mandatory"):
            mandatory.append(name)

    values = []
    for row in payload.get("values") or payload.get("specifications") or []:
        status = str(row.get("status") or "Published")
        if status.lower() != "published":
            logger.debug("taxonomy: skipping non-published spec value %r (%s)",
                         row.get("name"), status)
            continue
        parent = str(row.get("parent") or row.get("parent_specification") or "")
        values.append(FeatureEntry(
            id=str(row.get("id") or ""),
            name=str(row.get("name") or row.get("label") or ""),
            slug=str(row.get("slug") or ""),
            parent=parent,
            parent_id=parent_ids.get(parent, ""),
        ))
    return SpecCatalog(values=values, parents=parents, mandatory_parents=mandatory)


def _leading_int(text: str) -> int | None:
    """Returns the first integer found in the text, or None."""
    match = re.search(r"\d+", text or "")
    return int(match.group()) if match else None


def primary_exterior_color(text: str) -> str:
    """Reduces a two-tone exterior colour to its body segment, stripping body, roof and colour words."""
    raw = (text or "").strip()
    if not raw:
        return raw

    parts = [p.strip() for p in re.split(r"\s*[&/+]\s*|\s+and\s+", raw, flags=re.IGNORECASE) if p.strip()]
    if len(parts) < 2:
        return raw

    body = next((p for p in parts if re.search(r"\bbody\b", p, re.IGNORECASE)), parts[0])
    cleaned = re.sub(r"\b(body|roof|colour|color)\b", " ", body, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned or body


def _parent_for_label(label: str, catalog: SpecCatalog) -> str | None:
    """Maps a spec-sheet label to a parent specification via label hints, then fuzzy parent-name score."""
    normalized = re.sub(r"[^a-z ]", " ", (label or "").lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if normalized in _LABEL_HINTS:
        return _LABEL_HINTS[normalized]

    for hint, parent in _LABEL_HINTS.items():
        if hint in normalized:
            return parent

    best, best_score = None, 0.0
    for parent in catalog.parents:
        score = _score(parent, label)
        if score > best_score:
            best, best_score = parent, score
    return best if best_score >= 0.6 else None


def resolve_spec_value(value: str, parent: str, catalog: SpecCatalog) -> Resolution:
    """Resolves a value within one parent specification, matching numeric parents by their integer first."""
    options = catalog.for_parent(parent)
    if not options:
        return Resolution(parent, value, "missing", method="unknown_parent")

    if parent in _NUMERIC_PARENTS:
        wanted = _leading_int(value)
        if wanted is not None:
            for entry in options:
                if _leading_int(entry.name) == wanted:
                    return Resolution(parent, value, "matched", entry.id, entry.name, 0.97, "numeric")

    resolution = resolve_one(value, options, parent)
    return resolution


def resolve_specifications(
    raw_specifications: list[dict],
    catalog: SpecCatalog,
    folder_segments: list[str] | None = None,
    model_code: dict | None = None,
) -> dict:
    """Resolves spec rows to value ids, using model code colours and folder Body Type as fallbacks."""
    resolved: dict[str, dict] = {}
    unresolved: list[dict] = []
    unmapped: list[dict] = []

    for row in raw_specifications or []:
        label = str((row or {}).get("label") or "").strip()
        value = str((row or {}).get("value") or "").strip()
        if not label or not value:
            continue

        parent = _parent_for_label(label, catalog)
        if parent is None:
            unmapped.append({"label": label, "value": value})
            continue

        if parent in resolved:
            continue

        result = resolve_spec_value(value, parent, catalog)
        if result.status == "matched":
            resolved[parent] = {
                "parent": parent,
                "id": result.id,
                "name": result.name,
                "matched_from": f"{label}: {value}",
                "method": result.method,
            }
        else:
            unresolved.append({
                "parent": parent, "label": label, "value": value,
                "status": result.status, "candidates": result.candidates,
            })

    color_sources = [
        ("Color", primary_exterior_color((model_code or {}).get("exterior_color", ""))),
        ("Interior Color", (model_code or {}).get("interior_color", "")),
    ]
    for parent, value in color_sources:
        if parent in resolved or not value:
            continue
        result = resolve_spec_value(value, parent, catalog)
        if result.status == "matched":
            resolved[parent] = {
                "parent": parent,
                "id": result.id,
                "name": result.name,
                "matched_from": f"model code: {value}",
                "method": f"model_code_{result.method}",
            }
        elif result.candidates:
            unresolved.append({
                "parent": parent, "label": "model code", "value": value,
                "status": result.status, "candidates": result.candidates,
            })

    for segment in folder_segments or []:
        if "Body Type" in resolved:
            break
        result = resolve_spec_value(segment, "Body Type", catalog)
        if result.status == "matched":
            resolved["Body Type"] = {
                "parent": "Body Type",
                "id": result.id,
                "name": result.name,
                "matched_from": f"folder path: {segment}",
                "method": f"folder_path_{result.method}",
            }
            logger.info(
                "taxonomy: Body Type resolved from the folder path segment %r — spec "
                "sheets do not state body type, and it is the one mandatory field",
                segment,
            )

    missing_mandatory = [p for p in catalog.mandatory_parents if p not in resolved]
    if missing_mandatory:
        logger.info(
            "taxonomy: mandatory specification(s) %s unresolved — the form will block "
            "submission until a human sets them",
            missing_mandatory,
        )

    return {
        "specifications": list(resolved.values()),
        "specification_ids": {v["parent"]: v["id"] for v in resolved.values()},
        "unresolved": unresolved,
        "unmapped": unmapped,
        "mandatory_unfilled": missing_mandatory,
        "ready_to_prefill": not missing_mandatory,
    }


def parse_taxonomy(payload: list[dict]) -> list[TaxonomyEntry]:
    """Builds a TaxonomyEntry tree from JSON nodes, accepting several id, name and children key names."""

    def one(node: dict) -> TaxonomyEntry:
        """Converts one JSON node and its children recursively into a TaxonomyEntry."""
        entry_id = node.get("id") or node.get("value") or node.get("key") or ""
        name = node.get("name") or node.get("label") or node.get("title") or ""
        children_raw = node.get("children") or node.get("models") or node.get("trims") or []
        return TaxonomyEntry(
            id=str(entry_id),
            name=str(name),
            aliases=[str(a) for a in (node.get("aliases") or [])],
            children=[one(c) for c in children_raw],
        )

    return [one(n) for n in payload or []]
