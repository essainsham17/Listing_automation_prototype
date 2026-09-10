"""Saves, loads and fingerprints the LibraryIndex, and checks whether it still matches the store."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable

from app.nav.carkey.types import (CarKey, Designation, FieldValue, Silent,
                                  Stated, Unparseable)
from app.nav.library.model import LibraryIndex, ModelFolder, SheetFile, Variant
from app.nav.library.store import AssetStore, Entry, walk

SCHEMA = "app.nav.library.index/1"


class IndexUnusable(RuntimeError):
    """Base error for index operations this module refuses to perform."""


class EmptyIndexRefused(IndexUnusable):
    """Error raised when saving or loading an index that contains no model folders."""


class IndexCorrupt(IndexUnusable):
    """Error raised when an index file is missing, malformed, of another schema or disagrees with its header."""


def fingerprint_entries(entries: Iterable[Entry]) -> str:
    """Returns a blake2b hash of sorted entry paths, including size and mtime for files only."""
    digest = hashlib.blake2b(digest_size=16)
    lines = sorted(
        f"{e.path}\x1f{'d' if e.is_dir else 'f'}\x1f"
        f"{'' if e.is_dir else e.size}\x1f{'' if e.is_dir else e.mtime_ns}"
        for e in entries
    )
    for line in lines:
        digest.update(line.encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def tree_fingerprint(store: AssetStore, root: str = "") -> str:
    """Walks the store from a root and returns the fingerprint of every entry found."""
    entries: list[Entry] = []
    for _, children in walk(store, root):
        entries.extend(children)
    return fingerprint_entries(entries)


class Freshness(str, Enum):
    """Enum of index freshness states: FRESH or STALE."""
    FRESH = "fresh"
    STALE = "stale"


@dataclass(frozen=True)
class FreshnessCheck:
    """Freshness verdict with reason, remedy, expected and observed roots, and a root-mismatch flag."""

    state: Freshness
    reason: str
    remedy: str
    expected_root: str
    observed_root: str
    root_mismatch: bool = False

    @property
    def is_fresh(self) -> bool:
        """Returns True when the verdict state is FRESH."""
        return self.state is Freshness.FRESH


def _root_key(root: str) -> str:
    """Normalizes a root path to forward slashes, no trailing slash and casefolded text for comparison."""
    return root.replace("\\", "/").rstrip("/").casefold()


def check(index: LibraryIndex, store: AssetStore,
          root: str | None = None) -> FreshnessCheck:
    """Compares an index to a store by root, emptiness and tree fingerprint, returning a FreshnessCheck."""
    expected, observed = index.root, store.root
    if root is None:
        if _root_key(expected).startswith(_root_key(observed)):
            root = expected[len(observed):].strip("/")
        else:
            return FreshnessCheck(
                Freshness.STALE,
                reason=(f"this index was built from {expected!r}, but the "
                        f"store is rooted at {observed!r} — they are not the "
                        "same library"),
                remedy=("point the configuration back at the library the index "
                        "describes, or recrawl against this one; do NOT answer "
                        "lookups from this index"),
                expected_root=expected, observed_root=observed,
                root_mismatch=True)

    if index.is_empty:
        return FreshnessCheck(
            Freshness.STALE,
            reason="the index holds no model folders at all",
            remedy="recrawl; an empty index is never a valid description of "
                   "this library",
            expected_root=expected, observed_root=observed)

    current = tree_fingerprint(store, root)
    if current != index.tree_fingerprint:
        return FreshnessCheck(
            Freshness.STALE,
            reason=(f"the tree has changed since {index.crawled_at} "
                    f"(fingerprint {index.tree_fingerprint[:12]} -> "
                    f"{current[:12]})"),
            remedy="recrawl the library",
            expected_root=expected, observed_root=observed)

    return FreshnessCheck(
        Freshness.FRESH,
        reason=f"unchanged since {index.crawled_at}",
        remedy="",
        expected_root=expected, observed_root=observed)


def freshness(index: LibraryIndex, store: AssetStore,
              root: str | None = None) -> Freshness:
    """Returns only the FRESH or STALE state produced by check."""
    return check(index, store, root).state


def _encode_field(value: FieldValue) -> dict[str, Any]:
    """Encodes a FieldValue as a JSON-ready dict tagged with its kind and stated value type."""
    if isinstance(value, Stated):
        inner = value.value
        if isinstance(inner, Decimal):
            return {"k": "stated", "t": "decimal", "v": str(inner),
                    "raw": value.raw}
        if isinstance(inner, bool):
            return {"k": "stated", "t": "bool", "v": inner, "raw": value.raw}
        if isinstance(inner, int):
            return {"k": "stated", "t": "int", "v": inner, "raw": value.raw}
        return {"k": "stated", "t": "str", "v": str(inner), "raw": value.raw}
    if isinstance(value, Unparseable):
        return {"k": "unparseable", "raw": value.raw}
    if isinstance(value, Silent):
        return {"k": "silent"}
    raise IndexCorrupt(f"not a FieldValue: {value!r}")


def _decode_field(blob: Any) -> FieldValue:
    """Rebuilds a FieldValue from its JSON dict, raising IndexCorrupt on unknown kinds or types."""
    if not isinstance(blob, dict):
        raise IndexCorrupt(f"field is not an object: {blob!r}")
    kind = blob.get("k")
    if kind == "silent":
        return Silent()
    if kind == "unparseable":
        return Unparseable(raw=str(blob.get("raw", "")))
    if kind != "stated":
        raise IndexCorrupt(f"unknown field kind {kind!r}")
    type_name, raw_value = blob.get("t"), blob.get("v")
    if type_name == "decimal":
        return Stated(value=Decimal(str(raw_value)), raw=str(blob.get("raw", "")))
    if type_name == "int":
        return Stated(value=int(raw_value), raw=str(blob.get("raw", "")))
    if type_name == "bool":
        return Stated(value=bool(raw_value), raw=str(blob.get("raw", "")))
    if type_name == "str":
        return Stated(value=str(raw_value), raw=str(blob.get("raw", "")))
    raise IndexCorrupt(f"unknown stated type {type_name!r}")


_KEY_FIELDS = ("brand", "model_head", "trim", "fuel", "engine_l",
               "transmission", "model_year", "exterior", "interior",
               "vin_tag", "variant_suffix")


def _encode_key(key: CarKey) -> dict[str, Any]:
    """Encodes a CarKey as a dict of designation, per-field encodings and raw text."""
    return {
        "designation": {"tokens": list(key.designation.tokens),
                        "fingerprint": key.designation.fingerprint,
                        "raw": key.designation.raw},
        "fields": {name: _encode_field(key.field(name)) for name in _KEY_FIELDS},
        "raw": key.raw,
    }


def _decode_key(blob: dict[str, Any]) -> CarKey:
    """Rebuilds a CarKey from its JSON dict, treating missing fields as Silent."""
    designation = blob.get("designation") or {}
    fields = blob.get("fields") or {}
    return CarKey(
        designation=Designation(
            tokens=tuple(designation.get("tokens", ())),
            fingerprint=str(designation.get("fingerprint", "")),
            raw=str(designation.get("raw", "")),
        ),
        raw=str(blob.get("raw", "")),
        **{name: _decode_field(fields[name]) if name in fields else Silent()
           for name in _KEY_FIELDS},
    )


def _encode_variant(variant: Variant) -> dict[str, Any]:
    """Encodes a Variant and its spec sheets as a JSON-ready dict."""
    return {
        "path": variant.path,
        "name": variant.name,
        "exterior_raw": variant.exterior_raw,
        "interior_raw": variant.interior_raw,
        "vin_tag": variant.vin_tag,
        "photo_count": variant.photo_count,
        "sheets": [{"path": s.path, "name": s.name, "size": s.size,
                    "has_text_layer": s.has_text_layer}
                   for s in variant.sheets],
    }


def _decode_variant(blob: dict[str, Any]) -> Variant:
    """Rebuilds a Variant and its SheetFile tuple from a JSON dict."""
    return Variant(
        path=str(blob["path"]),
        name=str(blob["name"]),
        exterior_raw=blob.get("exterior_raw"),
        interior_raw=blob.get("interior_raw"),
        vin_tag=blob.get("vin_tag"),
        photo_count=int(blob.get("photo_count", 0)),
        sheets=tuple(
            SheetFile(path=str(s["path"]), name=str(s["name"]),
                      size=int(s.get("size", 0)),
                      has_text_layer=s.get("has_text_layer"))
            for s in blob.get("sheets", ())
        ),
    )


def to_json(index: LibraryIndex, grammar_version: str | None = None
            ) -> dict[str, Any]:
    """Returns the JSON-ready index with schema, root, fingerprint, grammar version, counts, drift, folders."""
    return {
        "schema": SCHEMA,
        "root": index.root,
        "root_key": _root_key(index.root),
        "crawled_at": index.crawled_at,
        "tree_fingerprint": index.tree_fingerprint,
        "grammar_version": grammar_version,
        "counts": {"folders": len(index.folders),
                   "variants": index.variant_count},
        "unparsed_segments": list(index.unparsed_segments),
        "folders": [
            {"path": f.path, "name": f.name,
             "path_segments": list(f.path_segments),
             "key": _encode_key(f.key),
             "variants": [_encode_variant(v) for v in f.variants]}
            for f in index.folders
        ],
    }


def from_json(blob: dict[str, Any]) -> LibraryIndex:
    """Rebuilds a LibraryIndex from JSON, checking schema and header counts and rejecting empty indexes."""
    if not isinstance(blob, dict):
        raise IndexCorrupt("index file is not a JSON object")
    schema = blob.get("schema")
    if schema != SCHEMA:
        raise IndexCorrupt(
            f"index schema is {schema!r}, this build reads {SCHEMA!r}")

    folders = tuple(
        ModelFolder(
            path=str(f["path"]),
            name=str(f["name"]),
            key=_decode_key(f["key"]),
            variants=tuple(_decode_variant(v) for v in f.get("variants", ())),
            path_segments=tuple(f.get("path_segments", ())),
        )
        for f in blob.get("folders", ())
    )

    counts = blob.get("counts") or {}
    expected_folders = counts.get("folders")
    if expected_folders is not None and expected_folders != len(folders):
        raise IndexCorrupt(
            f"header claims {expected_folders} model folders but the file "
            f"holds {len(folders)} — the index is truncated or edited")
    expected_variants = counts.get("variants")
    actual_variants = sum(len(f.variants) for f in folders)
    if expected_variants is not None and expected_variants != actual_variants:
        raise IndexCorrupt(
            f"header claims {expected_variants} variants but the file holds "
            f"{actual_variants}")

    index = LibraryIndex(
        root=str(blob.get("root", "")),
        folders=folders,
        tree_fingerprint=str(blob.get("tree_fingerprint", "")),
        crawled_at=str(blob.get("crawled_at", "")),
        unparsed_segments=tuple(blob.get("unparsed_segments", ())),
    )
    if index.is_empty:
        raise EmptyIndexRefused(
            f"the index at root {index.root!r} holds no model folders; refusing "
            "to load it. Recrawl the library.")
    return index


def save(index: LibraryIndex, path: str,
         grammar_version: str | None = None) -> str:
    """Atomically writes the index as JSON via a temporary file, refusing empty indexes; returns the path."""
    if index.is_empty:
        raise EmptyIndexRefused(
            f"refusing to save an index of {index.root!r} containing zero "
            "model folders. The library always has cars in it, so this is the "
            "record of a failed crawl — check that the root is correct and "
            "that the car-name grammar is available, then crawl again.")

    payload = to_json(index, grammar_version=grammar_version)
    destination = os.path.abspath(path)
    parent = os.path.dirname(destination)
    if parent:
        os.makedirs(parent, exist_ok=True)
    temporary = f"{destination}.partial"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    return destination


def load(path: str) -> LibraryIndex:
    """Reads an index JSON file and rebuilds it, raising IndexCorrupt if missing or not valid JSON."""
    destination = os.path.abspath(path)
    try:
        with open(destination, "r", encoding="utf-8") as handle:
            blob = json.load(handle)
    except FileNotFoundError as exc:
        raise IndexCorrupt(f"no index at {destination}") from exc
    except json.JSONDecodeError as exc:
        raise IndexCorrupt(f"index at {destination} is not valid JSON: {exc}") from exc
    return from_json(blob)


__all__ = [
    "SCHEMA",
    "EmptyIndexRefused",
    "Freshness",
    "FreshnessCheck",
    "IndexCorrupt",
    "check",
    "fingerprint_entries",
    "freshness",
    "from_json",
    "load",
    "save",
    "to_json",
    "tree_fingerprint",
]
