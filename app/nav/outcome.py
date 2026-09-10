"""Result types for a resolution question: matched, ambiguous, absent or blocked."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RejectedCandidate:
    """Record of an eliminated candidate with the field that eliminated it and a readable detail."""

    candidate_id: str
    label: str
    failed_on: str
    detail: str


@dataclass(frozen=True)
class Matched(Generic[T]):
    """Resolution holding the single surviving value, its identity basis and the rejected candidates."""

    value: T
    basis: str
    considered: tuple[RejectedCandidate, ...] = ()

    def is_answer(self) -> bool:
        """Returns True, since a match is a usable answer."""
        return True


@dataclass(frozen=True)
class Ambiguous(Generic[T]):
    """Resolution holding several surviving candidates, the field that would separate them and a remedy."""

    candidates: tuple[T, ...]
    discriminator: str
    remedy: str
    considered: tuple[RejectedCandidate, ...] = ()

    def is_answer(self) -> bool:
        """Returns False, since several candidates remain undecided."""
        return False


@dataclass(frozen=True)
class Absent(Generic[T]):
    """Resolution for no surviving candidate, with a reason, a remedy and the rejected candidates."""

    reason: str
    remedy: str
    considered: tuple[RejectedCandidate, ...] = ()

    def is_answer(self) -> bool:
        """Returns False, since no candidate survived."""
        return False


@dataclass(frozen=True)
class Blocked(Generic[T]):
    """Resolution for a question that could not be asked at all, with a reason and a remedy."""

    reason: str
    remedy: str

    def is_answer(self) -> bool:
        """Returns False, since the question could not be answered."""
        return False


Resolution = Matched[T] | Ambiguous[T] | Absent[T] | Blocked[T]


def is_answer(resolution) -> bool:
    """Returns True only when the resolution is a Matched instance."""
    return isinstance(resolution, Matched)
