"""Provenance types recording where a value came from and deriving its trust level."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar("T")


class SourceKind(str, Enum):
    """Enumeration of the sources a value can come from, from Salesforce columns to human reviewers."""

    SALESFORCE_COLUMN = "salesforce_column"
    CAR_STRING = "car_string"
    SPEC_SHEET = "spec_sheet"
    FOLDER_SEGMENT = "folder_segment"
    LEXICON = "lexicon"
    PHOTO_CLASSIFIER = "photo_classifier"
    LANGUAGE_MODEL = "language_model"
    HUMAN = "human"


class IdentityBasis(str, Enum):
    """Enumeration of how firmly the car's identity was known when a value was read, weakest first."""

    NONE = "none"
    MODEL_LEVEL = "model_level"
    SOLE_VARIANT = "sole_variant"
    COLOUR_UNIQUE = "colour_unique"
    HUMAN_PICKED = "human_picked"

    @property
    def is_unit_level(self) -> bool:
        """Returns whether the basis pins a specific variant: sole variant, colour unique or human picked."""
        return self in (IdentityBasis.SOLE_VARIANT,
                        IdentityBasis.COLOUR_UNIQUE,
                        IdentityBasis.HUMAN_PICKED)


class Trust(str, Enum):
    """Enumeration of the three trust levels: certain, assumed and proposed."""

    CERTAIN = "certain"
    ASSUMED = "assumed"
    PROPOSED = "proposed"


@dataclass(frozen=True)
class Locator:
    """Pointer to the evidence behind a value: path or column, plus optional page, span and detail."""

    where: str
    page: int | None = None
    span: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class Provenance:
    """Origin record for a value: source kind, locator, accepting rule, identity basis and lexicon alias."""
    source: SourceKind
    locator: Locator
    rule_id: str
    identity_basis: IdentityBasis
    lexicon_alias_id: str | None = None

    @property
    def trust(self) -> Trust:
        """Derives trust: PROPOSED for language models, CERTAIN for humans or unit-level identity, else ASSUMED."""
        if self.source is SourceKind.LANGUAGE_MODEL:
            return Trust.PROPOSED
        if self.source is SourceKind.HUMAN:
            return Trust.CERTAIN
        if not self.identity_basis.is_unit_level:
            return Trust.ASSUMED
        return Trust.CERTAIN


@dataclass(frozen=True)
class Claim(Generic[T]):
    """Value paired with its raw text and the provenance it was read under."""

    value: T
    raw: str
    provenance: Provenance

    @property
    def trust(self) -> Trust:
        """Returns the trust level derived from the claim's provenance."""
        return self.provenance.trust

    @property
    def is_certain(self) -> bool:
        """Returns whether the claim's derived trust is CERTAIN."""
        return self.trust is Trust.CERTAIN
