"""JSON-file listing store with atomic writes, rolling backups and corrupt-file recovery."""

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent.parent / "data" / "listings.json"))
BACKUP_DIR = DB_PATH.parent / "backups"
BACKUP_KEEP = int(os.environ.get("DB_BACKUP_KEEP", "5"))

_write_lock = RLock()


def now_iso() -> str:
    """Returns the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload) -> None:
    """Writes a payload as JSON to a temp file, fsyncs it, then atomically replaces the target path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False)

    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _backup(path: Path) -> None:
    """Copies an existing file to a timestamped backup, pruning copies beyond BACKUP_KEEP; logs OS errors."""
    if not path.exists():
        return
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        shutil.copy2(path, BACKUP_DIR / f"{path.name}.{stamp}.bak")

        existing = sorted(BACKUP_DIR.glob(f"{path.name}.*.bak"))
        for stale in existing[:-BACKUP_KEEP]:
            stale.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("db: backup of %s failed (continuing with write): %s", path.name, e)


def _newest_backup(path: Path) -> Path | None:
    """Returns the most recent backup file for the given path, or None when none exist."""
    if not BACKUP_DIR.exists():
        return None
    candidates = sorted(BACKUP_DIR.glob(f"{path.name}.*.bak"))
    return candidates[-1] if candidates else None


def read_json_or_recover(path: Path, default):
    """Reads JSON from a path, restoring the newest backup if unreadable; default when missing or unrecoverable."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        logger.error("db: %s is unreadable (%s) — attempting backup recovery", path.name, e)
        backup = _newest_backup(path)
        if backup is None:
            logger.error("db: no backup available for %s; returning empty state", path.name)
            return default
        try:
            recovered = json.loads(backup.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e2:
            logger.error("db: backup %s is also unreadable (%s)", backup.name, e2)
            return default

        try:
            path.replace(path.with_suffix(path.suffix + ".corrupt"))
            atomic_write_json(path, recovered)
            logger.warning("db: recovered %s from backup %s", path.name, backup.name)
        except OSError as e3:
            logger.error("db: could not restore %s from backup: %s", path.name, e3)
        return recovered


def load_listings() -> list[dict]:
    """Returns all listings from the DB file, or an empty list when the stored data is not a list."""
    data = read_json_or_recover(DB_PATH, [])
    if not isinstance(data, list):
        logger.error("db: listings.json is not a list (got %s) — treating as empty", type(data).__name__)
        return []
    return data


def save_listings(listings: list[dict]) -> None:
    """Backs up the DB file and atomically writes the given listings under the write lock."""
    with _write_lock:
        _backup(DB_PATH)
        atomic_write_json(DB_PATH, listings)


def get_listing(model_number: str) -> dict | None:
    """Returns the listing with the given model number, or None when no listing matches."""
    return next((l for l in load_listings() if l.get("model_number") == model_number), None)


def add_listing(listing: dict) -> None:
    """Appends a listing to the stored list and saves it under the write lock."""
    with _write_lock:
        listings = load_listings()
        listings.append(listing)
        save_listings(listings)


def update_listing(model_number: str, changes: dict) -> dict | None:
    """Merges changes into the matching listing, stamps updated_at, saves and returns it; None if absent."""
    with _write_lock:
        listings = load_listings()
        for entry in listings:
            if entry.get("model_number") == model_number:
                entry.update(changes)
                entry["updated_at"] = now_iso()
                save_listings(listings)
                return entry
    return None
