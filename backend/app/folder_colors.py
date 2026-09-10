"""Reads exterior and interior colours from a car folder name split on the word INSIDE."""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_SEPARATOR = re.compile(r"\bINSIDE?\b", re.IGNORECASE)

_VIN_SUFFIX = re.compile(
    r"[\s\-]+(?=[A-Z0-9]{11,17}\s*$)(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{11,17}\s*$",
    re.IGNORECASE,
)

_YEAR_SUFFIX = re.compile(r"[\s\-]+(?:19|20)?\d{2}\s*$")

_TRIM_JUNK = " -\t "


def _clean(part: str) -> str | None:
    """Collapses repeated whitespace and strips edge hyphens and spaces, returning None if nothing is left."""
    cleaned = re.sub(r"\s{2,}", " ", (part or "").strip(_TRIM_JUNK)).strip(_TRIM_JUNK)
    return cleaned or None


def parse_folder_colors(name: str) -> tuple[str | None, str | None]:
    """Returns (exterior, interior) from a folder name after stripping VIN and year, or (None, None)."""
    if not name:
        return None, None

    trimmed = _YEAR_SUFFIX.sub("", _VIN_SUFFIX.sub("", str(name)))

    parts = _SEPARATOR.split(trimmed, maxsplit=1)
    if len(parts) != 2:
        return None, None

    exterior, interior = _clean(parts[0]), _clean(parts[1])
    if not exterior or not interior:
        return None, None
    return exterior, interior


def colour_siblings(folder: Path) -> list[Path]:
    """Returns the sorted subfolders of a folder's parent, or just the folder itself if listing fails."""
    try:
        return sorted((p for p in folder.parent.iterdir() if p.is_dir()), key=lambda p: p.name)
    except OSError as e:
        logger.warning("folder_colors: couldn't list siblings of %s: %s", folder, e)
        return [folder]


def colour_options(folder: Path) -> list[dict]:
    """Lists sibling folders whose names state both colours as path, folder, exterior and interior dicts."""
    options = []
    for sibling in colour_siblings(folder):
        exterior, interior = parse_folder_colors(sibling.name)
        if exterior and interior:
            options.append({"path": str(sibling), "folder": sibling.name,
                            "exterior": exterior, "interior": interior})
    return options
