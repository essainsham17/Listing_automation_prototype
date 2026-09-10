"""Reads a PDF's embedded text layer with pdftotext as an alternative to rendering pages for vision."""

import logging
import shutil
import subprocess

from app import config

logger = logging.getLogger(__name__)


def pdftotext_available() -> bool:
    """Returns whether the pdftotext binary is on PATH."""
    return shutil.which("pdftotext") is not None


def extract_text_layer(pdf_path: str) -> str | None:
    """Returns the PDF's pdftotext -raw text, or None if disabled, unavailable, failed or under the word minimum."""
    if not config.PDF_TEXT_LAYER_ENABLED:
        return None
    if not pdftotext_available():
        logger.info("pdf_text: pdftotext is not on PATH — using the vision path")
        return None

    try:
        completed = subprocess.run(
            ["pdftotext", "-raw",
             "-l", str(config.MAX_PDF_PAGES),
             pdf_path, "-"],
            capture_output=True, check=True,
            timeout=config.PDF_RENDER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.warning("pdf_text: pdftotext timed out on %s — falling back to vision", pdf_path)
        return None
    except (subprocess.CalledProcessError, OSError) as e:
        logger.warning("pdf_text: pdftotext failed on %s (%s) — falling back to vision", pdf_path, e)
        return None

    text = completed.stdout.decode("utf-8", "ignore").strip()
    words = len(text.split())
    if words < config.PDF_TEXT_MIN_WORDS:
        logger.info("pdf_text: %s has only %d word(s) of text layer (min %d) — using vision",
                    pdf_path, words, config.PDF_TEXT_MIN_WORDS)
        return None

    logger.info("pdf_text: read %d word(s) from the text layer of %s", words, pdf_path)
    return text
