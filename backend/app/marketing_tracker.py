"""JSON-backed tracker of marketing photo, editing, spec sheet and SharePoint steps for new cars."""
import logging
from datetime import date
from pathlib import Path

from app.db import atomic_write_json, now_iso, read_json_or_recover
from app.stock_sync import get_pending_new_entry, load_pending_report
from threading import RLock

logger = logging.getLogger(__name__)

TRACKER_PATH = Path(__file__).parent.parent / "data" / "marketing_tracker.json"

_write_lock = RLock()

TICKABLE_STEPS = ("photographed", "edited", "sharepoint_uploaded")


def _load() -> list[dict]:
    """Reads tracker rows from the JSON file, returning an empty list if the data is not a list."""
    data = read_json_or_recover(TRACKER_PATH, [])
    if not isinstance(data, list):
        logger.error("marketing_tracker: %s is not a list (got %s) — treating as empty",
                     TRACKER_PATH.name, type(data).__name__)
        return []
    return data


def _save(rows: list[dict]) -> None:
    """Writes the tracker rows to the JSON file atomically under the write lock."""
    with _write_lock:
        atomic_write_json(TRACKER_PATH, rows)


def list_tracker() -> list[dict]:
    """Returns all tracker rows."""
    return _load()


def list_untracked_new() -> list[dict]:
    """Returns new cars from the pending stock sync report that are not yet tracked."""
    report = load_pending_report()
    if not report:
        return []
    tracked = {row["model_number"] for row in _load()}
    return [entry for entry in report.get("new", []) if entry.get("model_number") not in tracked]


def add_to_tracker(model_number: str) -> dict:
    """Creates a tracker row from a pending new entry, raising ValueError if absent or already tracked."""
    entry = get_pending_new_entry(model_number)
    if entry is None:
        raise ValueError(f"{model_number!r} is not in the current pending Stock Sync report")

    with _write_lock:
        rows = _load()
        if any(row["model_number"] == model_number for row in rows):
            raise ValueError(f"{model_number!r} is already being tracked")

        next_id = max((row["id"] for row in rows), default=0) + 1
        row = {
            "id": next_id,
            "model_number": model_number,
            "model_description": entry.get("product_description") or "",
            "exterior_color": entry.get("exterior_color") or "",
            "interior_color": entry.get("interior_color") or "",
            "vin": "",
            "priority": None,
            "assigned_to": "",
            "due_date": None,
            "photographed_at": None,
            "edited_at": None,
            "spec_generated_at": None,
            "sharepoint_uploaded_at": None,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        rows.append(row)
        _save(rows)
        return row


EDITABLE_FIELDS = ("vin", "priority", "assigned_to", "due_date")


def update_row(row_id: int, changes: dict) -> dict | None:
    """Applies allowed editable fields to the tracker row with the given id, returning it or None."""
    changes = {k: v for k, v in changes.items() if k in EDITABLE_FIELDS}
    with _write_lock:
        rows = _load()
        for row in rows:
            if row["id"] == row_id:
                row.update(changes)
                row["updated_at"] = now_iso()
                _save(rows)
                return row
    return None


def tick_step(row_id: int, step: str, done: bool = True) -> dict | None:
    """Sets or clears a step timestamp on a tracker row, raising ValueError for a non-tickable step."""
    if step not in TICKABLE_STEPS:
        raise ValueError(f"{step!r} is not tickable here — expected one of {TICKABLE_STEPS}")
    with _write_lock:
        rows = _load()
        for row in rows:
            if row["id"] == row_id:
                row[f"{step}_at"] = now_iso() if done else None
                row["updated_at"] = now_iso()
                _save(rows)
                return row
    return None


def set_spec_generated(vin: str, when: str | None = None) -> dict | None:
    """Stamps spec_generated_at on the row whose VIN matches, returning None if no row matches."""
    vin = (vin or "").strip().upper()
    if not vin:
        return None
    with _write_lock:
        rows = _load()
        for row in rows:
            if (row.get("vin") or "").strip().upper() == vin:
                row["spec_generated_at"] = when or now_iso()
                row["updated_at"] = now_iso()
                _save(rows)
                return row
    logger.warning("marketing_tracker: set_spec_generated got VIN %s, no tracker row matches it", vin)
    return None


def get_row_by_model_number(model_number: str) -> dict | None:
    """Returns the tracker row with the given model number, or None."""
    return next((r for r in _load() if r.get("model_number") == model_number), None)
