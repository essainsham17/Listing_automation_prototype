"""Crawls the photo library tree once into a LibraryIndex of model folders, variants and drift."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Iterable

from app.nav.carkey import parse_leaf
from app.nav.carkey.types import Parse
from app.nav.library.index import fingerprint_entries
from app.nav.library.model import LibraryIndex, ModelFolder, SheetFile, Variant
from app.nav.library.store import AssetStore, Entry, walk


FolderNameParser = Callable[[str], Parse]


class GrammarUnavailable(RuntimeError):
    """Error raised when no car-name grammar can be resolved for reading folder names."""


def resolve_parser() -> FolderNameParser:
    """Returns carkey's parse_model_folder, raising GrammarUnavailable if it cannot be imported."""
    try:
        from app.nav.carkey import parse_model_folder
    except ImportError as exc:  # pragma: no cover - guards a sibling package
        raise GrammarUnavailable(
            "app.nav.carkey.parse_model_folder is not importable, so no folder "
            "name can be read as a car and the index would come back empty. "
            "Pass crawl(..., parse=<callable>) explicitly, or repair carkey."
        ) from exc
    return parse_model_folder


def _parses_as_car(parse: Parse | None) -> bool:
    """Returns True when a parse succeeded and produced a non-empty designation."""
    return bool(parse is not None and parse.ok and parse.key.designation)


_PHOTO_SUFFIXES = frozenset({
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp",
    ".tif", ".tiff", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".orf",
})
_SHEET_SUFFIXES = frozenset({".pdf"})

_IGNORED_NAMES = frozenset({"thumbs.db", "desktop.ini", ".ds_store"})
_IGNORED_SUFFIXES = frozenset({".ini", ".db", ".tmp", ".lnk", ".url"})


DECLINED_COLOURS = (
    "the folder name states no '<EXTERIOR> INSIDE <INTERIOR>' pair, so which "
    "colour is outside and which is inside is not recoverable from it; the "
    "variant is indexed and its photographs are usable, but it can never be "
    "told apart from a sibling by colour"
)


class _Drift:
    """Collector of unique drift-report lines, each built from a kind, a subject and an optional note."""

    def __init__(self) -> None:
        """Starts an empty set of drift lines."""
        self._seen: set[str] = set()

    def add(self, kind: str, subject: str, note: str | None = None) -> None:
        """Records one drift line formatted from a kind, a subject and an optional note."""
        line = f"{kind}: {subject}" + (f" — {note}" if note else "")
        self._seen.add(line)

    def frozen(self) -> tuple[str, ...]:
        """Returns the collected drift lines as a sorted tuple."""
        return tuple(sorted(self._seen))


def _is_useful_file(entry: Entry) -> bool:
    """Returns False for sync and shell litter, matched by name like desktop.ini or by suffix like .tmp."""
    return not (entry.name.lower() in _IGNORED_NAMES
                or entry.suffix in _IGNORED_SUFFIXES)


def _build_variant(entry: Entry, children: tuple[Entry, ...],
                   drift: _Drift) -> Variant:
    """Builds a Variant from a colour folder: parses its name, collects PDF sheets, counts photos, logs drift."""
    exterior, interior, vin_tag = parse_leaf(entry.name)
    if not (exterior and interior):
        drift.add("variant-colours", entry.path, DECLINED_COLOURS)

    sheets: list[SheetFile] = []
    photos = 0
    for child in children:
        if child.is_dir:
            drift.add("below-variant", child.path,
                      "a directory inside a variant folder; its contents are "
                      "not indexed")
            continue
        if not _is_useful_file(child):
            continue
        if child.suffix in _SHEET_SUFFIXES:
            sheets.append(SheetFile(path=child.path, name=child.name,
                                    size=child.size))
        elif child.suffix in _PHOTO_SUFFIXES:
            photos += 1
        else:
            drift.add("unknown-file", child.path,
                      f"extension {child.suffix or '(none)'} is neither a "
                      "photograph nor a spec sheet")

    return Variant(
        path=entry.path,
        name=entry.name,
        exterior_raw=exterior,
        interior_raw=interior,
        vin_tag=vin_tag,
        sheets=tuple(sorted(sheets, key=lambda s: s.name)),
        photo_count=photos,
    )


def crawl(store: AssetStore, root: str = "",
          parse: FolderNameParser | None = None) -> LibraryIndex:
    """Walks the store once, classifies directories as model folders or shelves, and returns a LibraryIndex."""
    parser = parse if parse is not None else resolve_parser()
    drift = _Drift()

    tree: dict[str, tuple[Entry, ...]] = {}
    all_entries: list[Entry] = []
    for path, children in walk(store, root):
        tree[path] = children
        all_entries.extend(children)

    parsed: dict[str, Parse] = {}

    def parse_of(path: str, name: str) -> Parse:
        """Returns the cached parse of a directory name, running the parser on first request."""
        if path not in parsed:
            parsed[path] = parser(name)
        return parsed[path]

    def is_shelf(path: str, children: tuple[Entry, ...]) -> bool:
        """Returns True when a child folder parses as a car, contains subfolders and states no colour pair."""
        for child in children:
            if not child.is_dir:
                continue
            if not _parses_as_car(parse_of(child.path, child.name)):
                continue
            if not tree.get(child.path):
                continue
            if any(g.is_dir for g in tree.get(child.path, ())):
                exterior, interior, _ = parse_leaf(child.name)
                if exterior and interior:
                    continue
                return True
        return False

    folders: list[ModelFolder] = []
    shelves: list[str] = []
    pending = [root]
    while pending:
        path = pending.pop()
        children = tree.get(path, ())
        name = path.rsplit("/", 1)[-1] if path else ""

        result = parse_of(path, name) if name else None
        if _parses_as_car(result) and not is_shelf(path, children):
            folders.append(_build_model_folder(path, name, children, result,
                                               tree, root, drift))
            continue

        shelves.append(path)
        loose = [c for c in children if not c.is_dir and _is_useful_file(c)]
        if loose and name:
            drift.add("car-name", path,
                      f"{len(loose)} file(s) sit here but the folder name does "
                      "not parse as a car, so nothing in it can be matched")
        for child in children:
            if child.is_dir:
                pending.append(child.path)

    _report_dead_branches(shelves, folders, tree, root, drift)

    folders.sort(key=lambda f: f.path)
    return LibraryIndex(
        root=_library_identity(store, root),
        folders=tuple(folders),
        tree_fingerprint=fingerprint_entries(all_entries),
        crawled_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        unparsed_segments=drift.frozen(),
    )


def _build_model_folder(path: str, name: str, children: tuple[Entry, ...],
                        result: Parse, tree: dict[str, tuple[Entry, ...]],
                        root: str, drift: _Drift) -> ModelFolder:
    """Builds a ModelFolder with its variants and path segments, logging unresolved tokens and loose files."""
    for fragment in result.unresolved:
        drift.add("token", repr(fragment), f"in {path}")

    variants = [
        _build_variant(child, tree.get(child.path, ()), drift)
        for child in children if child.is_dir
    ]

    loose = [c for c in children if not c.is_dir and _is_useful_file(c)]
    if loose:
        drift.add("loose-file", path,
                  f"{len(loose)} file(s) sit in the model folder itself, "
                  "outside any variant, so no colour owns them")

    return ModelFolder(
        path=path,
        name=name,
        key=result.key,
        variants=tuple(sorted(variants, key=lambda v: v.path)),
        path_segments=_segments_above(path, root),
    )


def _segments_above(path: str, root: str) -> tuple[str, ...]:
    """Returns the shelf folder names between the crawl root and the given folder path."""
    inner = path[len(root):].strip("/") if root else path
    return tuple(inner.split("/")[:-1]) if inner else ()


def _report_dead_branches(shelves: Iterable[str], folders: list[ModelFolder],
                          tree: dict[str, tuple[Entry, ...]], root: str,
                          drift: _Drift) -> None:
    """Adds a drift line for each shallowest shelf that holds files but has no model folder beneath it."""
    deepest_first = sorted(tree, key=lambda p: p.count("/"), reverse=True)
    holds_files: set[str] = set()
    holds_car: set[str] = set()
    car_paths = {f.path for f in folders}
    for path in deepest_first:
        children = tree[path]
        if any(not e.is_dir and _is_useful_file(e) for e in children) or any(
                e.is_dir and e.path in holds_files for e in children):
            holds_files.add(path)
        if path in car_paths or any(
                e.is_dir and (e.path in car_paths or e.path in holds_car)
                for e in children):
            holds_car.add(path)

    named: list[str] = []
    for shelf in sorted(set(shelves), key=lambda p: (p.count("/"), p)):
        if shelf == root or shelf in holds_car:
            continue
        if shelf not in holds_files:
            continue
        if any(shelf.startswith(f"{n}/") for n in named):
            continue
        named.append(shelf)
        drift.add("dead-branch", shelf,
                  "holds files but no folder beneath it parses as a car")


def _library_identity(store: AssetStore, root: str) -> str:
    """Returns the store root joined with the crawl subpath, identifying which library was indexed."""
    base = store.root.rstrip("/")
    return f"{base}/{root.strip('/')}" if root.strip("/") else base


__all__ = [
    "DECLINED_COLOURS",
    "FolderNameParser",
    "GrammarUnavailable",
    "crawl",
    "resolve_parser",
]
