"""Lists the photos and spec sheet PDFs in a car's folder within the local photo library."""

import logging
from pathlib import Path

from app import config
from app.folder_finder import FolderNotFoundError, locate_car_folder

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"}


class PdfNotFoundError(RuntimeError):
    """Error raised when no single folder can be located for a car's search key."""
    pass


def _list_files_at_path(folder_path: Path) -> dict:
    """Recursively lists photos and PDFs under a folder, capping photos at the configured maximum."""
    if not folder_path.exists():
        raise RuntimeError(f"Folder not found: {folder_path}")

    photos, pdfs = [], []
    for path in folder_path.rglob("*"):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        relative_path = str(path.relative_to(folder_path))
        if ext in IMAGE_EXTENSIONS:
            photos.append({"name": path.name, "path": str(path), "relative_path": relative_path, "size": path.stat().st_size})
        elif ext == ".pdf":
            pdfs.append({"name": path.name, "path": str(path), "relative_path": relative_path})

    if len(photos) > config.MAX_ONEDRIVE_PHOTOS:
        logger.warning("list_photos_recursive: capping %d photos found down to %d under %s",
                        len(photos), config.MAX_ONEDRIVE_PHOTOS, folder_path)
        photos = photos[: config.MAX_ONEDRIVE_PHOTOS]

    logger.info("list_photos_recursive: found %d photo(s), %d pdf(s) under %s",
                len(photos), len(pdfs), folder_path)
    return {"folder_path": str(folder_path), "photos": photos, "pdfs": pdfs}


def list_photos_recursive(search_key: str) -> dict:
    """Locates the car folder for a search key and returns its photos and PDFs, else raises PdfNotFoundError."""
    root = Path(config.LOCAL_PHOTOS_ROOT)
    if not root.exists():
        raise RuntimeError(f"LOCAL_PHOTOS_ROOT does not exist: {root} — set it in .env to your synced photos folder.")
    try:
        folder_path = locate_car_folder(search_key, root)
    except FolderNotFoundError as e:
        raise PdfNotFoundError(str(e)) from e

    return _list_files_at_path(folder_path)


def read_bytes(path: str) -> bytes:
    """Returns the raw bytes of a local file."""
    return Path(path).read_bytes()
