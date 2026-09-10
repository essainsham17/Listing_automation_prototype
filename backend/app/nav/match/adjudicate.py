"""Decides which model folder and colour variant a car key identifies, by field elimination."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Callable, Iterable, Sequence

from app.nav.carkey.types import CarKey, FieldValue, Silent, Stated, Unparseable
from app.nav.match.compare import FieldComparison, compare_all, compare_field
from app.nav.match.roles import (CONTRADICTING, ROLE_TABLE, Role, eliminates,
                                 fields_with_role, role_of)
from app.nav.outcome import Absent, Ambiguous, Matched, RejectedCandidate
from app.nav.provenance import IdentityBasis
from app.nav.vocab import normalize

if TYPE_CHECKING:
    from app.nav.library.model import ModelFolder, Variant


Equivalent = Callable[[str, str, str], "str | None"]


__all__ = [
    "Remedy",
    "SEPARATOR_FIELDS",
    "adjudicate",
    "adjudicate_variant",
    "consultation_order",
    "remedy",
    "remedy_code",
    "remedy_detail",
]


class Remedy(str, Enum):
    """Enumeration of remedy codes naming what a human could supply to resolve an abstention."""

    ADD_FOLDER = "add_folder"
    """The library holds no folder for this car. Photograph it, or ratify a
    model_designation alias if the library already holds it under a name the
    feed does not use."""

    CONFIRM_FIELD = "confirm_field"
    """The library holds this model but every folder contradicts the feed on
    one field. Either the feed is wrong about the car or the library is
    missing that spec."""

    PICK_ONE_OF = "pick_one_of"
    """Several real candidates survived. A reviewer chooses, and the choice
    is recorded as IdentityBasis.HUMAN_PICKED."""

    PHOTOGRAPH_VARIANT = "photograph_variant"
    """The model folder is right and no variant in it is this colour. The
    car exists; its photographs do not."""

    NAME_THE_CAR = "name_the_car"
    """The car string itself states no name to match on. Nothing can be
    looked up until the feed says what the car is."""


_REMEDY_SEPARATOR = ": "


def remedy(code: Remedy, detail: str) -> str:
    """Formats a remedy code and its human-readable detail into one 'code: detail' string."""
    return f"{code.value}{_REMEDY_SEPARATOR}{detail}"


def remedy_code(text: str | None) -> Remedy | None:
    """Parses the leading remedy code from a remedy string, returning None if it names no known Remedy."""
    head, _, _rest = (text or "").partition(_REMEDY_SEPARATOR)
    try:
        return Remedy(head.strip())
    except ValueError:
        return None


def remedy_detail(text: str | None) -> str:
    """Returns the human detail of a remedy string, stripping the prefix only when it is a known code."""
    head, separator, detail = (text or "").partition(_REMEDY_SEPARATOR)
    if not separator:
        return head
    try:
        Remedy(head.strip())
    except ValueError:
        return text or ""
    return detail


_TABLE_ORDER: tuple[str, ...] = tuple(rule.field for rule in ROLE_TABLE)

SEPARATOR_FIELDS: tuple[str, ...] = fields_with_role(Role.SEPARATOR)

_VARIANT_ATTRIBUTE: dict[str, str] = {
    "exterior": "exterior_raw",
    "interior": "interior_raw",
}


def consultation_order(fields: Iterable[str]) -> tuple[str, ...]:
    """Orders field names with ROLE_TABLE fields first in table order, then the rest in input order."""
    present = tuple(fields)
    known = set(present)
    ordered = tuple(field for field in _TABLE_ORDER if field in known)
    seen = set(ordered)
    return ordered + tuple(field for field in present if field not in seen)


def _first_disqualifier(comparisons: dict[str, FieldComparison]
                        ) -> FieldComparison | None:
    """Returns the first comparison in consultation order whose verdict eliminates under its role, or None."""
    for field in consultation_order(comparisons):
        comparison = comparisons[field]
        if eliminates(role_of(field), comparison.verdict):
            return comparison
    return None


def _reject(candidate_id: str, label: str,
            comparison: FieldComparison) -> RejectedCandidate:
    """Builds a RejectedCandidate naming the failing field and its comparison sentence."""
    return RejectedCandidate(candidate_id=candidate_id, label=label,
                             failed_on=comparison.field,
                             detail=comparison.sentence)


def _label(candidate) -> str:
    """Returns a candidate's display label from its name, else its path, else '?'."""
    return getattr(candidate, "name", "") or getattr(candidate, "path", "") or "?"


def _identifier(candidate) -> str:
    """Returns a candidate's identifier from its path, falling back to its label."""
    return getattr(candidate, "path", "") or _label(candidate)


def _mark(key: CarKey, field: str) -> str:
    """Builds a token for a key's value in one field (stated, unreadable or silent) to test distinctness."""
    if field == "designation":
        return f"d:{key.designation.fingerprint}"
    value: FieldValue = key.field(field)
    if isinstance(value, Stated):
        inner = value.value
        if isinstance(inner, Decimal):
            return f"s:{inner.normalize()}"
        return f"s:{normalize.fingerprint(str(inner))}"
    if isinstance(value, Unparseable):
        return f"u:{normalize.fingerprint(value.raw)}"
    return "silent"


def _folder_colour_mark(folder: "ModelFolder", field: str) -> frozenset[str]:
    """Returns the set of colour fingerprints (or 'silent') a folder's variants hold in one separator field."""
    attribute = _VARIANT_ATTRIBUTE.get(field)
    if attribute is None:
        return frozenset()
    return frozenset(
        normalize.fingerprint(getattr(variant, attribute, None)) or "silent"
        for variant in folder.variants)


def _discriminating_field(marks_by_field: dict[str, tuple], fallback: str) -> str:
    """Returns the first non-evidence field in table order where survivor marks differ, else the fallback."""
    for field in consultation_order(marks_by_field):
        if role_of(field) is Role.EVIDENCE_ONLY:
            continue
        if len(set(marks_by_field[field])) > 1:
            return field
    return fallback


def _separable(marks_by_field: dict[str, tuple]) -> bool:
    """Returns True if any non-evidence-only field has differing marks across the survivors."""
    return any(len(set(values)) > 1
               for field, values in marks_by_field.items()
               if role_of(field) is not Role.EVIDENCE_ONLY)


def _quoted(values: Iterable[str]) -> str:
    """Joins distinct values in first-seen order as repr strings, or returns 'nothing' when empty."""
    return ", ".join(repr(v) for v in dict.fromkeys(values)) or "nothing"


def _tally(rejects: Sequence[RejectedCandidate]) -> str:
    """Summarises rejections as per-field counts such as '5 on designation', in consultation order."""
    counts: dict[str, int] = {}
    for reject in rejects:
        counts[reject.failed_on] = counts.get(reject.failed_on, 0) + 1
    return ", ".join(f"{counts[field]} on {field}"
                     for field in consultation_order(counts))


def adjudicate(target: CarKey,
               candidates: Sequence["ModelFolder"],
               equivalent: Equivalent | None = None,
               ) -> "Matched[ModelFolder] | Ambiguous[ModelFolder] | Absent":
    """Resolves a car key to one model folder by field elimination and colour, else Ambiguous or Absent."""
    if not candidates:
        return Absent(
            reason="no model folder was offered for comparison",
            remedy=remedy(Remedy.ADD_FOLDER,
                          f"nothing in the library was shortlisted for "
                          f"{target.designation.raw or target.raw!r}; check the "
                          "shortlist, then photograph this car into a new model "
                          "folder if the library really does not hold it"))

    survivors: list["ModelFolder"] = []
    rejects: list[RejectedCandidate] = []

    for candidate in candidates:
        comparisons = compare_all(target, candidate.key, equivalent)
        killer = _first_disqualifier(comparisons)
        if killer is None:
            survivors.append(candidate)
        else:
            rejects.append(_reject(_identifier(candidate), _label(candidate),
                                   killer))

    if not survivors:
        return _nothing_survived(target, tuple(rejects))

    if len(survivors) == 1:
        (only,) = survivors
        return Matched(value=only, basis=IdentityBasis.MODEL_LEVEL.value,
                       considered=tuple(rejects))

    kept, colour_rejects = _separate_folders_by_colour(target, survivors,
                                                       equivalent)
    considered = tuple(rejects) + colour_rejects

    if not kept:
        field = _one_field((reject.failed_on for reject in colour_rejects),
                           _first_separator())
        return Absent(
            reason=(f"{len(survivors)} folders match this specification but none "
                    f"holds a variant in {_stated_raw(target.field(field))}"),
            remedy=remedy(Remedy.PHOTOGRAPH_VARIANT,
                          "photograph this colour into one of the matching "
                          "folders, or confirm the colour on the vehicle"),
            considered=considered)

    if len(kept) == 1:
        (only,) = kept
        return Matched(value=only, basis=IdentityBasis.MODEL_LEVEL.value,
                       considered=considered)

    marks: dict[str, tuple] = {
        field: tuple(_mark(folder.key, field) for folder in kept)
        for field in _TABLE_ORDER
    }
    marks.update({field: tuple(_folder_colour_mark(folder, field)
                               for folder in kept)
                  for field in SEPARATOR_FIELDS})

    discriminator = _discriminating_field(marks, fallback=_first_separator())
    names = _quoted(_label(folder) for folder in kept)

    if _separable(marks):
        detail = (f"{len(kept)} folders survived every field: {names}. State the "
                  f"{discriminator} in the feed, or have a reviewer choose.")
    else:
        detail = (f"{len(kept)} folders survived every field and nothing this "
                  f"grammar reads separates them: "
                  f"{_quoted(_identifier(folder) for folder in kept)}. A reviewer "
                  "must open them and choose; the library may hold one car filed "
                  "twice.")

    return Ambiguous(candidates=tuple(kept), discriminator=discriminator,
                     remedy=remedy(Remedy.PICK_ONE_OF, detail),
                     considered=considered)


def _first_separator() -> str:
    """Returns the first SEPARATOR field from the role table, defaulting to 'exterior'."""
    for field in SEPARATOR_FIELDS:
        return field
    return "exterior"


def _stated_raw(value: FieldValue) -> str:
    """Formats a field value for a reason sentence: quoted raw, raw marked unreadable, or an unstated colour."""
    if isinstance(value, Stated):
        return repr(value.raw)
    if isinstance(value, Unparseable):
        return f"{value.raw!r} (unreadable)"
    return "a colour the feed does not state"


def _nothing_survived(target: CarKey,
                      rejects: tuple[RejectedCandidate, ...]) -> Absent:
    """Builds the Absent for zero survivors, choosing a NAME_THE_CAR, ADD_FOLDER or CONFIRM_FIELD remedy."""
    if not target.designation.fingerprint:
        return Absent(
            reason=(f"{target.raw or '(empty)'!r} states no name to match on, so "
                    f"every one of the {len(rejects)} folders considered failed "
                    "the designation gate"),
            remedy=remedy(Remedy.NAME_THE_CAR,
                          "supply a Model Code or Product Description that names "
                          "the car; nothing can be looked up without one"),
            considered=rejects)

    failed_fields = {reject.failed_on for reject in rejects}
    name_fields = {"designation", "brand", "model_head", "trim"}

    if failed_fields <= name_fields:
        return Absent(
            reason=(f"no folder is named for {target.designation.raw!r}; "
                    f"{len(rejects)} considered, {_tally(rejects)}"),
            remedy=remedy(Remedy.ADD_FOLDER,
                          f"photograph this car into a new model folder, or — if "
                          f"the library already holds it under another spelling — "
                          f"ratify a model_designation alias for "
                          f"{target.designation.raw!r} in vocab/lexicon.yaml"),
            considered=rejects)

    spec_fields = tuple(field for field in consultation_order(failed_fields)
                        if field not in name_fields)
    return Absent(
        reason=(f"the library holds {target.designation.raw!r} but no folder "
                f"agrees on {', '.join(spec_fields)}; {len(rejects)} considered, "
                f"{_tally(rejects)}"),
        remedy=remedy(Remedy.CONFIRM_FIELD,
                      f"confirm {', '.join(spec_fields)} against the vehicle — "
                      "either the feed is wrong about this car or the library "
                      "does not hold this specification yet"),
        considered=rejects)


def _separate_folders_by_colour(target: CarKey,
                                folders: Sequence["ModelFolder"],
                                equivalent: Equivalent | None,
                                ) -> tuple[tuple["ModelFolder", ...],
                                           tuple[RejectedCandidate, ...]]:
    """Drops folders whose every variant contradicts the target's colour; returns kept folders and rejections."""
    kept: list["ModelFolder"] = []
    rejects: list[RejectedCandidate] = []

    for folder in folders:
        variants = tuple(folder.variants)
        if not variants:
            kept.append(folder)
            continue

        contradictions = tuple(
            comparison for comparison in
            (_first_colour_contradiction(target, variant, equivalent)
             for variant in variants)
            if comparison is not None)

        if len(contradictions) < len(variants):
            kept.append(folder)
            continue

        field = _one_field((comparison.field for comparison in contradictions),
                           fallback=_first_separator())
        rejects.append(RejectedCandidate(
            candidate_id=_identifier(folder),
            label=_label(folder),
            failed_on=field,
            detail=(f"{field}: none of its {len(variants)} variants states "
                    f"{_stated_raw(target.field(field))} — they state "
                    f"{_quoted(c.right_raw or '?' for c in contradictions)}")))

    return tuple(kept), tuple(rejects)


def _one_field(fields: Iterable[str], fallback: str) -> str:
    """Returns the single distinct field name in the iterable, or the fallback if there are zero or several."""
    unique = set(fields)
    if len(unique) == 1:
        (only,) = unique
        return only
    return fallback


def _colour_field(raw: str | None) -> FieldValue[str]:
    """Wraps a variant's cleaned raw colour as a Stated field value, or Silent when it is empty or None."""
    text = normalize.clean(raw)
    return Stated(value=text, raw=text) if text else Silent()


def _compare_separators(target: CarKey, variant: "Variant",
                        equivalent: Equivalent | None
                        ) -> tuple[FieldComparison, ...]:
    """Compares the target's SEPARATOR fields against a variant's raw colours, in table order."""
    comparisons: list[FieldComparison] = []
    for field in SEPARATOR_FIELDS:
        attribute = _VARIANT_ATTRIBUTE.get(field)
        if attribute is None:
            continue
        comparisons.append(compare_field(
            field, target.field(field),
            _colour_field(getattr(variant, attribute, None)),
            equivalent))
    return tuple(comparisons)


def _first_colour_contradiction(target: CarKey, variant: "Variant",
                                equivalent: Equivalent | None
                                ) -> FieldComparison | None:
    """Returns the first separator comparison whose verdict contradicts the target, or None."""
    for comparison in _compare_separators(target, variant, equivalent):
        if comparison.verdict in CONTRADICTING:
            return comparison
    return None


def adjudicate_variant(target: CarKey,
                       folder: "ModelFolder",
                       equivalent: Equivalent | None = None,
                       ) -> "Matched[Variant] | Ambiguous[Variant] | Absent":
    """Resolves a car key to one colour variant of a folder by colour elimination, else Ambiguous or Absent."""
    variants = tuple(folder.variants)

    if not variants:
        return Absent(
            reason=(f"{_label(folder)!r} holds no variant folders, so there is "
                    "no colour to resolve"),
            remedy=remedy(Remedy.PHOTOGRAPH_VARIANT,
                          f"add a '<EXTERIOR> INSIDE <INTERIOR>' folder under "
                          f"{_identifier(folder)}"))

    survivors: list["Variant"] = []
    rejects: list[RejectedCandidate] = []

    for variant in variants:
        contradiction = _first_colour_contradiction(target, variant, equivalent)
        if contradiction is None:
            survivors.append(variant)
        else:
            rejects.append(_reject(_identifier(variant), _label(variant),
                                   contradiction))

    if not survivors:
        field = _one_field((reject.failed_on for reject in rejects),
                           _first_separator())
        return Absent(
            reason=(f"{_label(folder)!r} is the right model, but none of its "
                    f"{len(variants)} variants is "
                    f"{_stated_raw(target.field(field))}"),
            remedy=remedy(Remedy.PHOTOGRAPH_VARIANT,
                          "photograph this colour into the folder, or confirm "
                          "the colour on the vehicle; the spec sheet in this "
                          "folder still applies at model level"),
            considered=tuple(rejects))

    if len(survivors) == 1:
        (only,) = survivors
        basis = (IdentityBasis.SOLE_VARIANT if len(variants) == 1
                 else IdentityBasis.COLOUR_UNIQUE)
        return Matched(value=only, basis=basis.value, considered=tuple(rejects))

    marks: dict[str, tuple] = {
        field: tuple(_mark_variant(variant, field) for variant in survivors)
        for field in SEPARATOR_FIELDS
    }
    discriminator = _discriminating_field(marks, fallback=_first_separator())

    silent = tuple(variant for variant in survivors
                   if not getattr(variant, "states_colours", True))
    if len(silent) == len(survivors):
        detail = (f"{len(survivors)} variant folders survive and none of their "
                  "names states a readable '<EXTERIOR> INSIDE <INTERIOR>' pair, "
                  "so no field can separate them: a reviewer must open them and "
                  "choose")
    elif silent:
        detail = (f"{len(survivors)} variant folders survive because "
                  f"{len(silent)} of them name no readable "
                  f"'<EXTERIOR> INSIDE <INTERIOR>' pair: "
                  f"{_quoted(_label(variant) for variant in silent)}. Rename "
                  "them to state their colours, or have a reviewer choose.")
    elif not _separable(marks):
        detail = (f"{len(survivors)} variant folders state the same colours and "
                  f"differ only by their reference tag, which identifies no car: "
                  f"{_quoted(_label(variant) for variant in survivors)}. A "
                  "reviewer must open them and choose.")
    else:
        detail = (f"{len(survivors)} colours survive: "
                  f"{_quoted(_label(variant) for variant in survivors)}. "
                  f"State the {discriminator} in the feed, or have a reviewer "
                  "choose.")

    return Ambiguous(candidates=tuple(survivors), discriminator=discriminator,
                     remedy=remedy(Remedy.PICK_ONE_OF, detail),
                     considered=tuple(rejects))


def _mark_variant(variant: "Variant", field: str) -> str:
    """Builds a distinctness token from a variant's raw colour in one separator field, or 'silent'."""
    attribute = _VARIANT_ATTRIBUTE.get(field)
    raw = getattr(variant, attribute, None) if attribute else None
    text = normalize.fingerprint(raw)
    return f"s:{text}" if text else "silent"
