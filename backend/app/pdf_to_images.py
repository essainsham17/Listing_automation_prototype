"""Renders PDF pages to JPEG images for vision models, dropping the trailing photo pages."""

import base64
import logging
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from app import config

logger = logging.getLogger(__name__)

RENDER_DPI = 200
MAX_DIMENSION = 1600


def _page_white_fraction(page_path: Path) -> float:
    """Returns the fraction of a rendered page's greyscale pixels above the white threshold."""
    arr = np.array(Image.open(page_path).convert("L"))
    return float((arr > config.WHITE_PIXEL_THRESHOLD).mean())


def _page_colourfulness(page_path: Path) -> float:
    """Returns the mean per-pixel spread between the R, G and B channels of a rendered page."""
    arr = np.array(Image.open(page_path).convert("RGB")).astype("int16")
    return float((arr.max(axis=2) - arr.min(axis=2)).mean())


def _is_photo_page(page_path: Path) -> bool:
    """Returns whether a page is a photo: white fraction below and colourfulness at or above their thresholds."""
    return (_page_white_fraction(page_path) < config.SPEC_PAGE_DROP_THRESHOLD
            and _page_colourfulness(page_path) >= config.PHOTO_COLOUR_THRESHOLD)


def _find_spec_page_boundary(page_paths: list[Path]) -> int:
    """Returns the index of the first pair of consecutive photo pages after page one, or the page count if none."""
    if len(page_paths) < 2:
        return len(page_paths)

    photo = [_is_photo_page(p) for p in page_paths]
    for i in range(1, len(photo) - 1):
        if photo[i] and photo[i + 1]:
            return i
    return len(page_paths)


def pdf_to_page_images_b64(pdf_path: str) -> list[str]:
    """Renders a PDF with pdftoppm, cuts trailing photo pages, and returns resized JPEG pages as base64."""
    with tempfile.TemporaryDirectory() as tmp:
        prefix = str(Path(tmp) / "page")
        try:
            subprocess.run(
                ["pdftoppm", "-jpeg", "-r", str(RENDER_DPI),
                 "-l", str(config.MAX_PDF_PAGES),
                 pdf_path, prefix],
                check=True, timeout=config.PDF_RENDER_TIMEOUT_SECONDS,
                capture_output=True,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"PDF rendering timed out after {config.PDF_RENDER_TIMEOUT_SECONDS}s — "
                "the file may be corrupt or unusually large."
            ) from e
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace") if e.stderr else ""
            raise RuntimeError(f"PDF rendering failed (pdftoppm exit {e.returncode}): {stderr[:500]}") from e

        page_files = sorted(Path(tmp).glob("page-*.jpg"))
        if not page_files:
            raise RuntimeError("PDF rendered zero pages — file may be empty, corrupt, or password-protected.")
        logger.info("pdf_to_page_images_b64: rendered %d page(s) from %s", len(page_files), pdf_path)

        boundary = _find_spec_page_boundary(page_files)
        if boundary < len(page_files):
            logger.info("pdf_to_page_images_b64: kept %d/%d page(s), dropped %d photo page(s) after the spec section",
                        boundary, len(page_files), len(page_files) - boundary)
            page_files = page_files[:boundary]

        encoded = []
        for page_file in page_files:
            img = Image.open(page_file)
            img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
            resized_path = page_file.with_suffix(".resized.jpg")
            img.save(resized_path, quality=90)
            encoded.append(base64.b64encode(resized_path.read_bytes()).decode())
        return encoded
