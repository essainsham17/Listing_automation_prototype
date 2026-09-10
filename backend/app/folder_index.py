"""Builds, saves and loads a flat JSON index of library folders holding spec sheets or photos."""

import json
import logging
from pathlib import Path

from app.local_photos import IMAGE_EXTENSIONS

logger = logging.getLogger(__name__)

DEFAULT_INDEX_PATH = Path(__file__).parent.parent / "data" / "folder_index.json"


def build_index(root: Path) -> list[dict]:
    """Lists folders under root that directly hold PDFs or images, with path, segments, PDF names and image count."""
    root = Path(root)
    leaves = []
    for dirpath in sorted(p for p in root.rglob("*") if p.is_dir()):
        pdf_names = sorted(p.name for p in dirpath.glob("*.pdf"))
        image_count = sum(
            1 for p in dirpath.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
        )
        if not pdf_names and not image_count:
            continue
        leaves.append({
            "path": str(dirpath),
            "segments": list(dirpath.relative_to(root).parts),
            "pdf_names": pdf_names,
            "image_count": image_count,
        })
    with_pdf = sum(1 for leaf in leaves if leaf["pdf_names"])
    logger.info("folder_index: crawled %s, found %d car folder(s) — %d with a spec sheet, "
                "%d with photos only", root, len(leaves), with_pdf, len(leaves) - with_pdf)
    return leaves


def save_index(index: list[dict], path: Path = DEFAULT_INDEX_PATH) -> None:
    """Writes the folder index to a JSON file, creating its parent directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2))


def load_index(path: Path = DEFAULT_INDEX_PATH) -> list[dict] | None:
    """Reads the folder index from JSON, returning None if the file is missing or unreadable."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("folder_index: couldn't read %s (%s), treating as not built", path, e)
        return None
