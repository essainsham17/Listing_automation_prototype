"""Finds candidate library folders for a car key and composes model and colour adjudication."""

from __future__ import annotations

import re
import weakref
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum

from app.nav.carkey.types import CarKey, Stated
from app.nav.library.index import FreshnessCheck
from app.nav.library.model import LibraryIndex, ModelFolder, Variant
from app.nav.outcome import (Absent, Ambiguous, Blocked, Matched,
                             RejectedCandidate, Resolution)
from app.nav.vocab import normalize


class Bucket(str, Enum):
    """Enum naming the lookup (designation, brand or sheet filename stem) that surfaced a candidate."""

    DESIGNATION = "designation_fingerprint"
    BRAND = "brand_token"
    SHEET_STEM = "sheet_filename_stem"


@dataclass(frozen=True)
class Retrieved:
    """Retrieved model folder paired with the buckets and bucket keys that surfaced it."""

    folder: ModelFolder
    buckets: tuple[Bucket, ...]
    keys: tuple[str, ...]

    @property
    def why(self) -> str:
        """Returns a readable sentence naming the folder and the buckets that caused it to be looked at."""
        names = ", ".join(b.value for b in self.buckets)
        return (f"{self.folder.name!r} was looked at because it shares "
                f"{names} with the target")


_LOGO_TAIL = re.compile(r"[\s.\-]*(?:no|with\s*out)\s+logo\s*\Z", re.IGNORECASE)


def _stem(filename: str) -> str:
    """Returns the filename with its final extension removed."""
    dot = filename.rfind(".")
    return filename[:dot] if dot > 0 else filename


def _sheet_keys(filename: str) -> tuple[str, ...]:
    """Returns the fingerprints of a sheet filename stem, with and without a trailing no-logo note."""
    stem = _stem(filename)
    keys = [normalize.fingerprint(stem)]
    trimmed = _LOGO_TAIL.sub("", stem)
    if trimmed != stem:
        keys.append(normalize.fingerprint(trimmed))
    return tuple(dict.fromkeys(k for k in keys if k))


def brand_key(key: CarKey) -> str:
    """Returns the brand fingerprint for a car key, falling back to its first designation token."""
    brand = key.brand
    if isinstance(brand, Stated):
        fingerprint = normalize.fingerprint(str(brand.value))
        if fingerprint:
            return fingerprint
    tokens = key.designation.tokens
    return tokens[0] if tokens else ""


@dataclass(frozen=True)
class Buckets:
    """Library index inverted into designation, brand and sheet-stem lookup maps of model folders."""

    by_designation: dict[str, tuple[ModelFolder, ...]]
    by_brand: dict[str, tuple[ModelFolder, ...]]
    by_sheet_stem: dict[str, tuple[ModelFolder, ...]]
    tree_fingerprint: str
    folder_count: int

    @classmethod
    def of(cls, index: LibraryIndex) -> "Buckets":
        """Builds the designation, brand and sheet-stem maps from every folder in a library index."""
        designation: dict[str, list[ModelFolder]] = defaultdict(list)
        brand: dict[str, list[ModelFolder]] = defaultdict(list)
        stems: dict[str, list[ModelFolder]] = defaultdict(list)

        for folder in index.folders:
            fingerprint = folder.key.designation.fingerprint
            if fingerprint:
                designation[fingerprint].append(folder)

            key = brand_key(folder.key)
            if key:
                brand[key].append(folder)

            for variant in folder.variants:
                for sheet in variant.sheets:
                    for stem_key in _sheet_keys(sheet.name):
                        stems[stem_key].append(folder)

        return cls(
            by_designation=_freeze(designation),
            by_brand=_freeze(brand),
            by_sheet_stem=_freeze(stems),
            tree_fingerprint=index.tree_fingerprint,
            folder_count=len(index.folders),
        )


def _freeze(mapping: dict[str, list[ModelFolder]]
            ) -> dict[str, tuple[ModelFolder, ...]]:
    """Deduplicates each bucket's folders by path and returns them as path-sorted tuples."""
    frozen: dict[str, tuple[ModelFolder, ...]] = {}
    for key, folders in mapping.items():
        unique = {folder.path: folder for folder in folders}
        frozen[key] = tuple(unique[path] for path in sorted(unique))
    return frozen


_MEMO: dict[int, Buckets] = {}


def buckets_for(index: LibraryIndex) -> Buckets:
    """Returns memoised bucket maps for an index, rebuilding when its fingerprint or folder count changes."""
    token = id(index)
    cached = _MEMO.get(token)
    if (cached is not None
            and cached.tree_fingerprint == index.tree_fingerprint
            and cached.folder_count == len(index.folders)):
        return cached
    built = Buckets.of(index)
    _MEMO[token] = built
    weakref.finalize(index, _MEMO.pop, token, None)
    return built


def retrieve(target: CarKey, index: LibraryIndex,
             buckets: Buckets | None = None) -> tuple[Retrieved, ...]:
    """Probes all buckets for a target car key and returns found folders with their buckets, in path order."""
    maps = buckets if buckets is not None else buckets_for(index)

    probes: list[tuple[Bucket, str]] = []
    designation = target.designation.fingerprint
    if designation:
        probes.append((Bucket.DESIGNATION, designation))

    brand = brand_key(target)
    if brand:
        probes.append((Bucket.BRAND, brand))

    for text in (target.raw, target.designation.raw):
        stem_key = normalize.fingerprint(text)
        if stem_key:
            probes.append((Bucket.SHEET_STEM, stem_key))

    source = {Bucket.DESIGNATION: maps.by_designation,
              Bucket.BRAND: maps.by_brand,
              Bucket.SHEET_STEM: maps.by_sheet_stem}

    found: dict[str, tuple[ModelFolder, list[Bucket], list[str]]] = {}
    for bucket, key in probes:
        for folder in source[bucket].get(key, ()):
            entry = found.get(folder.path)
            if entry is None:
                found[folder.path] = (folder, [bucket], [key])
                continue
            _, seen_buckets, seen_keys = entry
            if bucket not in seen_buckets:
                seen_buckets.append(bucket)
            if key not in seen_keys:
                seen_keys.append(key)

    return tuple(
        Retrieved(folder=folder, buckets=tuple(bucket_list), keys=tuple(key_list))
        for path, (folder, bucket_list, key_list) in sorted(found.items())
    )


def candidates(target: CarKey, index: LibraryIndex,
               buckets: Buckets | None = None) -> list[ModelFolder]:
    """Returns the model folders retrieved for a target car key, in path order, for adjudication."""
    return [item.folder for item in retrieve(target, index, buckets)]


@dataclass(frozen=True)
class Located:
    """Result pairing a model folder with an optional colour variant."""

    folder: ModelFolder
    variant: Variant | None = None

    @property
    def has_variant(self) -> bool:
        """Returns whether a colour variant is set."""
        return self.variant is not None

    @property
    def label(self) -> str:
        """Returns the folder name, followed by the variant name when a variant is set."""
        if self.variant is None:
            return self.folder.name
        return f"{self.folder.name} / {self.variant.name}"

    @property
    def path(self) -> str:
        """Returns the variant path when a variant is set, otherwise the folder path."""
        return self.variant.path if self.variant is not None else self.folder.path


def _merge_considered(*groups: tuple[RejectedCandidate, ...]
                      ) -> tuple[RejectedCandidate, ...]:
    """Merges rejected-candidate groups in order, deduplicating on candidate id and failed field."""
    merged: dict[tuple[str, str], RejectedCandidate] = {}
    for group in groups:
        for rejected in group:
            merged.setdefault((rejected.candidate_id, rejected.failed_on),
                              rejected)
    return tuple(merged.values())


def locate(target: CarKey, index: LibraryIndex, *,
           freshness: FreshnessCheck | None = None,
           buckets: Buckets | None = None,
           equivalent=None) -> Resolution[Located]:
    """Returns Blocked on an empty or stale index, else adjudicates model then colour into a Located resolution."""
    if index.is_empty:
        return Blocked(
            reason=(f"the index of {index.root!r} holds no model folders, so "
                    "there was nothing to look through"),
            remedy=("recrawl the library; an empty index is the record of a "
                    "failed crawl, never a library with no cars in it"))

    if freshness is not None and not freshness.is_fresh:
        return Blocked(
            reason=f"the index cannot be trusted: {freshness.reason}",
            remedy=freshness.remedy or "recrawl the library before looking again")

    from app.nav.match.adjudicate import adjudicate, adjudicate_variant

    pool = candidates(target, index, buckets)

    model = (adjudicate(target, pool, equivalent=equivalent)
             if equivalent is not None else adjudicate(target, pool))

    if isinstance(model, Blocked):
        return model
    if isinstance(model, Absent):
        return model
    if isinstance(model, Ambiguous):
        return Ambiguous(
            candidates=tuple(Located(folder=folder) for folder in model.candidates),
            discriminator=model.discriminator,
            remedy=model.remedy,
            considered=model.considered)

    if not isinstance(model, Matched):
        raise TypeError(
            f"match.adjudicate returned {type(model).__name__}, which is not "
            "one of the four outcomes this module knows how to compose. "
            "Refusing to guess which of them it meant.")

    folder: ModelFolder = model.value
    colour = (adjudicate_variant(target, folder, equivalent=equivalent)
              if equivalent is not None else adjudicate_variant(target, folder))

    if isinstance(colour, Blocked):
        return colour

    if isinstance(colour, Matched):
        return Matched(
            value=Located(folder=folder, variant=colour.value),
            basis=colour.basis,
            considered=_merge_considered(model.considered, colour.considered))

    if isinstance(colour, Ambiguous):
        return Ambiguous(
            candidates=tuple(Located(folder=folder, variant=variant)
                             for variant in colour.candidates),
            discriminator=colour.discriminator,
            remedy=colour.remedy,
            considered=_merge_considered(model.considered, colour.considered))

    if not isinstance(colour, Absent):
        raise TypeError(
            f"match.adjudicate_variant returned {type(colour).__name__}, "
            "which is not one of the four outcomes this module knows how to "
            "compose. Refusing to guess which of them it meant.")

    return Absent(
        reason=(f"the model folder {folder.name!r} matched, but no colour "
                f"variant of it did: {colour.reason}"),
        remedy=colour.remedy,
        considered=_merge_considered(model.considered, colour.considered))


__all__ = [
    "Bucket",
    "Buckets",
    "Located",
    "Retrieved",
    "brand_key",
    "buckets_for",
    "candidates",
    "locate",
    "retrieve",
]
