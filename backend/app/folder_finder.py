"""Finds the single colour-variant folder for a Model Code in the local photo library."""

import logging
import threading
import time
from pathlib import Path

from app import config
from app.nav import (Absent, Ambiguous, Blocked, LibraryIndex, LocalStore,
                     Matched, StoreError, crawl, locate, parse_car_string)
from app.nav.library.crawl import GrammarUnavailable

logger = logging.getLogger(__name__)


class FolderNotFoundError(RuntimeError):
    """Error raised when a Model Code does not resolve to exactly one library folder."""
    pass


_INDEX_LOCK = threading.Lock()
_INDEX_CACHE: dict[str, tuple[float, LocalStore, LibraryIndex]] = {}


def _library(root: Path) -> tuple[LocalStore, LibraryIndex]:
    """Returns the store and crawled library index for a root, caching non-empty crawls for the TTL."""
    store = LocalStore(str(root))
    key = store.root
    now = time.monotonic()

    with _INDEX_LOCK:
        cached = _INDEX_CACHE.get(key)
        if cached is not None and (now - cached[0]) < config.FOLDER_INDEX_TTL_SECONDS:
            return cached[1], cached[2]

        index = crawl(store)

        if index.is_empty:
            logger.error("folder_finder: crawl of %s produced zero model folders — "
                         "not caching it", key)
            return store, index

        _INDEX_CACHE[key] = (now, store, index)
        logger.info("folder_finder: crawled %s — %d model folder(s), %d variant(s), "
                    "%d drift note(s)", key, len(index.folders), index.variant_count,
                    len(index.unparsed_segments))
        return store, index


def _candidate_lines(resolution) -> str:
    """Joins the labels of a resolution's candidate folders into one semicolon-separated string."""
    return "; ".join(located.label for located in resolution.candidates)


def locate_car_folder(model_number: str, root: Path) -> Path:
    """Returns the one folder matching a Model Code, raising FolderNotFoundError for any other outcome."""
    key = parse_car_string(model_number).key

    try:
        store, index = _library(root)
    except GrammarUnavailable as exc:
        raise FolderNotFoundError(
            f"Could not look for {model_number}: the car-name grammar is not "
            f"available, so no folder name can be read as a car ({exc}). This is "
            "a broken build, not a missing car — do not re-photograph anything."
        ) from exc
    except StoreError as exc:
        raise FolderNotFoundError(
            f"Could not look for {model_number}: the photo library at {root} could "
            f"not be read ({exc}). Check that LOCAL_PHOTOS_ROOT points at the "
            "synced library and that it has finished syncing. Nothing was "
            "searched, so this is NOT evidence that the car has no folder."
        ) from exc

    resolution = locate(key, index)

    if isinstance(resolution, Matched):
        found = Path(store.locate(resolution.value.path))
        logger.info("folder_finder: %s matched %s (basis=%s, %d candidate(s) rejected)",
                    model_number, resolution.value.label, resolution.basis,
                    len(resolution.considered))
        return found

    if isinstance(resolution, Blocked):
        logger.error("folder_finder: %s BLOCKED — %s", model_number, resolution.reason)
        raise FolderNotFoundError(
            f"Could not look for {model_number}: {resolution.reason}. "
            f"Remedy: {resolution.remedy}. This is a configuration problem, not a "
            "missing car — the library was not searched."
        )

    if isinstance(resolution, Ambiguous):
        logger.warning("folder_finder: %s AMBIGUOUS on %s among %d candidate(s)",
                       model_number, resolution.discriminator,
                       len(resolution.candidates))
        raise FolderNotFoundError(
            f"{model_number} matches {len(resolution.candidates)} folders and "
            f"nothing separates them: {_candidate_lines(resolution)}. The field "
            f"that would settle it is '{resolution.discriminator}'. "
            f"Remedy: {resolution.remedy}. Refusing to pick one — picking under "
            "ambiguity is what put a Corolla Cross on a Corolla spec sheet."
        )

    if not isinstance(resolution, Absent):
        raise FolderNotFoundError(
            f"Could not resolve {model_number}: app.nav.locate returned "
            f"{type(resolution).__name__}, which is not one of the four outcomes "
            "this function knows how to answer. Refusing to guess."
        )

    logger.info("folder_finder: %s ABSENT — %s", model_number, resolution.reason)
    raise FolderNotFoundError(
        f"No folder for {model_number}: {resolution.reason}. "
        f"Remedy: {resolution.remedy}."
    )
