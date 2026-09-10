"""Compares fields of two car keys and returns per-field verdicts, applying no match policy."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.nav.carkey.types import (CarKey, Designation, FieldValue, Silent,
                                  Stated, Unparseable)
from app.nav.vocab import normalize


class Verdict(str, Enum):
    """Enumeration of the possible outcomes of comparing one field between two cars."""
    EQUAL = "equal"
    EQUAL_VIA_ALIAS = "equal_via_alias"
    DISAGREE = "disagree"
    PREFIX_ONLY = "prefix_only"
    ONE_SILENT = "one_silent"
    BOTH_SILENT = "both_silent"
    UNCOMPARABLE = "uncomparable"


@dataclass(frozen=True)
class FieldComparison:
    """Result of comparing one field: the verdict, both raw values and any alias id."""
    field: str
    verdict: Verdict
    left_raw: str | None
    right_raw: str | None
    alias_id: str | None = None

    @property
    def sentence(self) -> str:
        """Returns a one-line human explanation of the comparison, worded according to its verdict."""
        if self.verdict is Verdict.DISAGREE:
            return f"{self.field}: {self.left_raw!r} vs {self.right_raw!r}"
        if self.verdict is Verdict.PREFIX_ONLY:
            return (f"{self.field}: {self.left_raw!r} is only a prefix of "
                    f"{self.right_raw!r} — not the same model")
        if self.verdict is Verdict.ONE_SILENT:
            stated = self.left_raw or self.right_raw
            return f"{self.field}: only one side states it ({stated!r})"
        if self.verdict is Verdict.BOTH_SILENT:
            return f"{self.field}: neither side states it"
        if self.verdict is Verdict.UNCOMPARABLE:
            return f"{self.field}: could not be read"
        if self.verdict is Verdict.EQUAL_VIA_ALIAS:
            return f"{self.field}: {self.left_raw!r} = {self.right_raw!r} (alias {self.alias_id})"
        return f"{self.field}: {self.left_raw!r}"


def _raw(field: FieldValue) -> str | None:
    """Returns the raw text of a Stated or Unparseable field value, or None for silence."""
    if isinstance(field, Stated):
        return field.raw
    if isinstance(field, Unparseable):
        return field.raw
    return None


def compare_field(field: str, left: FieldValue, right: FieldValue,
                  equivalent=None) -> FieldComparison:
    """Compares one field of two cars, handling unreadable, silent, numeric, fingerprint and alias cases."""
    lraw, rraw = _raw(left), _raw(right)

    if isinstance(left, Unparseable) or isinstance(right, Unparseable):
        return FieldComparison(field, Verdict.UNCOMPARABLE, lraw, rraw)

    lsilent, rsilent = isinstance(left, Silent), isinstance(right, Silent)
    if lsilent and rsilent:
        return FieldComparison(field, Verdict.BOTH_SILENT, None, None)
    if lsilent or rsilent:
        return FieldComparison(field, Verdict.ONE_SILENT, lraw, rraw)

    lv, rv = left.value, right.value

    if isinstance(lv, Decimal) and isinstance(rv, Decimal):
        return FieldComparison(field,
                               Verdict.EQUAL if lv == rv else Verdict.DISAGREE,
                               lraw, rraw)
    if isinstance(lv, int) and isinstance(rv, int):
        return FieldComparison(field,
                               Verdict.EQUAL if lv == rv else Verdict.DISAGREE,
                               lraw, rraw)

    lf, rf = normalize.fingerprint(str(lv)), normalize.fingerprint(str(rv))
    if lf and lf == rf:
        return FieldComparison(field, Verdict.EQUAL, lraw, rraw)

    if equivalent is not None:
        alias_id = equivalent(field, str(lv), str(rv))
        if alias_id:
            return FieldComparison(field, Verdict.EQUAL_VIA_ALIAS, lraw, rraw, alias_id)

    return FieldComparison(field, Verdict.DISAGREE, lraw, rraw)


def compare_designation(left: Designation, right: Designation) -> FieldComparison:
    """Compares two designations by fingerprint as equal, prefix-only, disagreeing, or one side silent."""
    if not left.fingerprint or not right.fingerprint:
        return FieldComparison("designation", Verdict.ONE_SILENT,
                               left.raw or None, right.raw or None)

    if left.fingerprint == right.fingerprint:
        return FieldComparison("designation", Verdict.EQUAL, left.raw, right.raw)

    if (left.fingerprint.startswith(right.fingerprint)
            or right.fingerprint.startswith(left.fingerprint)):
        return FieldComparison("designation", Verdict.PREFIX_ONLY, left.raw, right.raw)

    return FieldComparison("designation", Verdict.DISAGREE, left.raw, right.raw)


COMPARABLE_FIELDS = ("brand", "model_head", "trim", "fuel", "engine_l",
                     "transmission", "model_year", "exterior", "interior",
                     "vin_tag")


def compare_all(target: CarKey, candidate: CarKey,
                equivalent=None) -> dict[str, FieldComparison]:
    """Compares the designation and every comparable field of two car keys, returning a dict by field name."""
    result = {"designation": compare_designation(target.designation,
                                                 candidate.designation)}
    for name in COMPARABLE_FIELDS:
        result[name] = compare_field(name, target.field(name),
                                     candidate.field(name), equivalent)
    return result
