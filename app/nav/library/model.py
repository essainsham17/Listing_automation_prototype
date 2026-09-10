"""Frozen data model of the crawled photo library: index, model folders, variants and sheets."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.nav.carkey.types import CarKey


@dataclass(frozen=True)
class SheetFile:
    """Spec-sheet PDF in a variant folder, with its path, name, size and text-layer flag."""

    path: str
    name: str
    size: int
    has_text_layer: bool | None = None


@dataclass(frozen=True)
class Variant:
    """One colour combination of a model folder, with raw colours, VIN tag, sheets and photo count."""

    path: str
    name: str
    exterior_raw: str | None
    interior_raw: str | None
    vin_tag: str | None
    sheets: tuple[SheetFile, ...] = ()
    photo_count: int = 0

    @property
    def has_sheet(self) -> bool:
        """Returns whether the variant holds at least one spec sheet."""
        return bool(self.sheets)

    @property
    def states_colours(self) -> bool:
        """Returns whether both the exterior and interior raw colour values are present and non-empty."""
        return bool(self.exterior_raw and self.interior_raw)


@dataclass(frozen=True)
class ModelFolder:
    """One car specification folder with its parsed car key, colour variants and path segments."""

    path: str
    name: str
    key: CarKey
    variants: tuple[Variant, ...] = ()
    path_segments: tuple[str, ...] = ()

    @property
    def body_type_hint(self) -> str | None:
        """Returns the first path segment naming a known body type, uppercased, or None."""
        known = {"SEDAN", "SUV", "PICKUP", "BUS", "MPV", "CARGO VAN", "COUPE", "HATCHBACK"}
        for seg in self.path_segments:
            if seg.strip().upper() in known:
                return seg.strip().upper()
        return None


@dataclass(frozen=True)
class LibraryIndex:
    """Crawled library snapshot with model folders, tree fingerprint, crawl time and unparsed segments."""

    root: str
    folders: tuple[ModelFolder, ...]
    tree_fingerprint: str
    crawled_at: str
    unparsed_segments: tuple[str, ...] = ()

    @property
    def variant_count(self) -> int:
        """Returns the total number of variants across all model folders."""
        return sum(len(f.variants) for f in self.folders)

    @property
    def is_empty(self) -> bool:
        """Returns whether the index holds no model folders."""
        return not self.folders
