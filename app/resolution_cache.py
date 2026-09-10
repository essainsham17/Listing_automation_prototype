"""Persistent on-disk cache of AI-resolved values, such as colour names mapped to catalog colours."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import RLock

from app.db import atomic_write_json

logger = logging.getLogger(__name__)

CACHE_PATH = Path(
    os.environ.get("AI_RESOLUTION_CACHE_PATH", Path(__file__).parent.parent / "data" / "reference" / "ai_resolution_cache.json")
)

_lock = RLock()
_cache: dict | None = None


def _key(raw_value: str, parent: str) -> str:
    """Builds the cache key from the parent and the trimmed, upper-cased raw value."""
    return f"{parent}|{(raw_value or '').strip().upper()}"


def _load() -> dict:
    """Loads the cache file into memory once, using an empty cache if the file is missing or unreadable."""
    global _cache
    if _cache is not None:
        return _cache
    if not CACHE_PATH.exists():
        _cache = {}
        return _cache
    try:
        import json
        _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("resolution_cache: could not read %s (%s) — continuing without a cache", CACHE_PATH, e)
        _cache = {}
    return _cache


def lookup(raw_value: str, parent: str, valid_names: set[str] | None = None) -> dict | None:
    """Returns the cached entry for a raw value, or None if absent or a matched name is not in valid_names."""
    entry = _load().get(_key(raw_value, parent))
    if entry is None:
        return None
    if entry.get("status") == "matched" and valid_names is not None and entry.get("name") not in valid_names:
        logger.info("resolution_cache: cached answer %r for %r is stale (not in the current %s catalog) — "
                     "re-resolving live, entry kept on disk", entry.get("name"), raw_value, parent)
        return None
    return entry


def record(raw_value: str, parent: str, status: str, name: str | None, id_: str | None, method: str) -> None:
    """Upserts a timestamped resolution entry and writes the cache file atomically, logging any write failure."""
    from app.db import now_iso

    with _lock:
        cache = _load()
        cache[_key(raw_value, parent)] = {
            "raw_value": raw_value,
            "parent": parent,
            "status": status,
            "name": name,
            "id": id_,
            "resolved_at": now_iso(),
            "method": method,
        }
        try:
            atomic_write_json(CACHE_PATH, cache)
        except Exception as e:
            logger.warning("resolution_cache: failed to persist %s (%s)", CACHE_PATH, e)
