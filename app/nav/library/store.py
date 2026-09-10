"""Storage abstraction for the photo library, with a local-directory store and a tree walker."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Protocol, runtime_checkable


class StoreError(RuntimeError):
    """Base error raised when the store cannot list, stat or read a path."""


class StoreUnreachable(StoreError):
    """Error raised when the library root does not exist or cannot be reached."""


class NotFound(StoreError):
    """Error raised when a requested path does not exist in the store or is not a folder."""


@dataclass(frozen=True)
class Entry:
    """Store entry for a file or directory with store-relative path, name, size and mtime."""

    path: str
    name: str
    is_dir: bool
    size: int
    mtime_ns: int

    @property
    def suffix(self) -> str:
        """Returns the lowercased file extension including the dot, or an empty string."""
        dot = self.name.rfind(".")
        return self.name[dot:].lower() if dot > 0 else ""


def join(parent: str, name: str) -> str:
    """Joins a store-relative parent path and a name without a leading slash at the root."""
    return f"{parent}/{name}" if parent else name


@runtime_checkable
class AssetStore(Protocol):
    """Protocol defining the storage operations the rest of the system may use."""

    @property
    def root(self) -> str:
        """Returns a stable identity for the library the store serves."""
        ...

    def list_children(self, path: str) -> tuple[Entry, ...]:
        """Returns the immediate children of a path, sorted by name."""
        ...

    def stat(self, path: str) -> Entry:
        """Returns metadata for one entry, raising NotFound if it is missing."""
        ...

    def exists(self, path: str) -> bool:
        """Returns whether something exists at a path."""
        ...

    def read_bytes(self, path: str) -> bytes:
        """Returns the full contents of a file."""
        ...

    def locate(self, path: str) -> str:
        """Returns a human-openable location string for a store path."""
        ...


class LocalStore:
    """AssetStore implementation backed by a directory on the local machine."""

    def __init__(self, root: str) -> None:
        """Stores the absolute form of the given root directory."""
        self._root = os.path.abspath(os.fspath(root))

    def __repr__(self) -> str:
        """Returns a debug representation showing the root directory."""
        return f"LocalStore({self._root!r})"

    @property
    def root(self) -> str:
        """Returns the root as an absolute path with forward slashes and no trailing slash."""
        return self._root.replace("\\", "/").rstrip("/")

    def _absolute(self, path: str) -> str:
        """Converts a store-relative path into an absolute local filesystem path."""
        rel = (path or "").strip("/")
        return os.path.join(self._root, *rel.split("/")) if rel else self._root

    @staticmethod
    def _entry(path: str, name: str, stat_result: os.stat_result,
               is_dir: bool) -> Entry:
        """Builds an Entry from a path, name, stat result and directory flag, with size 0 for directories."""
        return Entry(path=path, name=name, is_dir=is_dir,
                     size=0 if is_dir else stat_result.st_size,
                     mtime_ns=stat_result.st_mtime_ns)

    def list_children(self, path: str) -> tuple[Entry, ...]:
        """Lists a folder's immediate entries sorted by name, mapping OS errors to store errors."""
        target = self._absolute(path)
        try:
            with os.scandir(target) as scan:
                raw = list(scan)
        except FileNotFoundError as exc:
            if not path:
                raise StoreUnreachable(
                    f"library root does not exist: {self.root}") from exc
            raise NotFound(f"no such folder: {path or '<root>'}") from exc
        except NotADirectoryError as exc:
            raise NotFound(f"not a folder: {path or '<root>'}") from exc
        except OSError as exc:
            raise StoreError(
                f"could not list {path or '<root>'}: {exc}") from exc

        entries: list[Entry] = []
        for item in raw:
            try:
                is_dir = item.is_dir(follow_symlinks=False)
                info = item.stat(follow_symlinks=False)
            except OSError:
                continue
            entries.append(self._entry(join(path, item.name), item.name,
                                       info, is_dir))
        entries.sort(key=lambda e: e.name)
        return tuple(entries)

    def stat(self, path: str) -> Entry:
        """Returns an Entry for a path, raising NotFound or StoreError on failure."""
        target = self._absolute(path)
        try:
            info = os.stat(target)
        except FileNotFoundError as exc:
            raise NotFound(f"no such path: {path or '<root>'}") from exc
        except OSError as exc:
            raise StoreError(f"could not stat {path or '<root>'}: {exc}") from exc
        name = path.rsplit("/", 1)[-1] if path else os.path.basename(self._root)
        return self._entry(path, name, info, os.path.isdir(target))

    def exists(self, path: str) -> bool:
        """Returns whether the path exists on the local filesystem."""
        return os.path.exists(self._absolute(path))

    def read_bytes(self, path: str) -> bytes:
        """Reads a file's bytes, raising NotFound or StoreError on failure."""
        target = self._absolute(path)
        try:
            with open(target, "rb") as handle:
                return handle.read()
        except FileNotFoundError as exc:
            raise NotFound(f"no such file: {path}") from exc
        except OSError as exc:
            raise StoreError(f"could not read {path}: {exc}") from exc

    def locate(self, path: str) -> str:
        """Returns the absolute local path with forward slashes."""
        return self._absolute(path).replace("\\", "/")


def walk(store: AssetStore, path: str = "") -> Iterable[tuple[str, tuple[Entry, ...]]]:
    """Yields each folder path with its children in depth-first name order, skipping revisited paths."""
    stack = [path]
    seen: set[str] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        children = store.list_children(current)
        yield current, children
        for child in reversed(children):
            if child.is_dir:
                stack.append(child.path)
