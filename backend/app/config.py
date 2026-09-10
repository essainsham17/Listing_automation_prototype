"""Environment-overridable settings for models, providers, timeouts, limits and feature switches."""

import logging
import os
import sys

from dotenv import load_dotenv

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(_BACKEND_DIR, ".env"))


def _env_float(name: str, default: float) -> float:
    """Returns the named environment variable as a float, or the default when it is unset or empty."""
    val = os.environ.get(name)
    return float(val) if val else default


def _env_int(name: str, default: int) -> int:
    """Returns the named environment variable as an int, or the default when it is unset or empty."""
    val = os.environ.get(name)
    return int(val) if val else default


def _env_bool(name: str, default: bool) -> bool:
    """Returns whether the named env variable is 1, true, yes or on, or the default when unset or empty."""
    val = os.environ.get(name)
    return val.strip().lower() in ("1", "true", "yes", "on") if val else default


EXTRACTION_MODEL = os.environ.get("EXTRACTION_MODEL", "google/gemma-4-31b-it:free")
EXTRACTION_BASE_URL = os.environ.get("EXTRACTION_BASE_URL", "https://openrouter.ai/api/v1")
EXTRACTION_TIMEOUT_SECONDS = _env_float("EXTRACTION_TIMEOUT_SECONDS", 300.0)
EXTRACTION_MAX_RETRIES = _env_int("EXTRACTION_MAX_RETRIES", 2)
EXTRACTION_MAX_TOKENS = _env_int("EXTRACTION_MAX_TOKENS", 16000)
EXTRACTION_FALLBACK_ATTEMPTS = _env_int("EXTRACTION_FALLBACK_ATTEMPTS", 1)

EXTRACTION_RECOVER_TRUNCATED = _env_bool("EXTRACTION_RECOVER_TRUNCATED", True)

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "free").strip().lower()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1/")


def use_anthropic() -> bool:
    """Returns True when LLM_PROVIDER is anthropic and ANTHROPIC_API_KEY is set."""
    return LLM_PROVIDER == "anthropic" and bool(os.environ.get("ANTHROPIC_API_KEY"))


MATCH_PROVIDER = os.environ.get("MATCH_PROVIDER", "").strip().lower()
MATCH_ANTHROPIC_MODEL = os.environ.get("MATCH_ANTHROPIC_MODEL", "claude-sonnet-5")


def use_anthropic_for_match() -> bool:
    """Returns True when MATCH_PROVIDER, or LLM_PROVIDER if empty, is anthropic and ANTHROPIC_API_KEY is set."""
    return (MATCH_PROVIDER or LLM_PROVIDER) == "anthropic" and bool(
        os.environ.get("ANTHROPIC_API_KEY")
    )

EDENAI_BASE_URL = os.environ.get("EDENAI_BASE_URL", "https://api.edenai.run/v3")
EDENAI_MODEL = os.environ.get("EDENAI_MODEL", "google/gemma-4-31b-it")
EDENAI_MATCH_MODEL = os.environ.get("EDENAI_MATCH_MODEL", EDENAI_MODEL)


def edenai_key() -> str | None:
    """Returns the EDENAI_API_KEY environment value, or None when it is unset or empty."""
    return os.environ.get("EDENAI_API_KEY") or None


def use_edenai() -> bool:
    """Returns True when an Eden AI key is set and neither the Claude CLI nor Anthropic is selected."""
    if use_claude_cli() or use_anthropic():
        return False
    return bool(edenai_key())


def use_edenai_for_match() -> bool:
    """Returns True when an Eden AI key is set and matching is not routed to the Claude CLI or Anthropic."""
    if use_claude_cli_for_match() or use_anthropic_for_match():
        return False
    return bool(edenai_key())


GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
LLM_BACKEND = os.environ.get("LLM_BACKEND", "free").strip().lower()
CLI_MODEL = os.environ.get("CLI_MODEL", "claude-haiku-4-5-20251001").strip()
CLI_TIMEOUT_SECONDS = _env_float("CLI_TIMEOUT_SECONDS", 900.0)


def use_claude_cli() -> bool:
    """Returns True when LLM_BACKEND is set to claude_cli."""
    return LLM_BACKEND == "claude_cli"


MATCH_BACKEND = os.environ.get("MATCH_BACKEND", "").strip().lower()
MATCH_CLI_MODEL = os.environ.get("MATCH_CLI_MODEL", "claude-sonnet-5")


def use_claude_cli_for_match() -> bool:
    """Returns True when MATCH_BACKEND, or LLM_BACKEND if empty, is claude_cli."""
    return (MATCH_BACKEND or LLM_BACKEND) == "claude_cli"
MATCH_MODEL = os.environ.get("MATCH_MODEL", EXTRACTION_MODEL)
MATCH_BASE_URL = os.environ.get("MATCH_BASE_URL", EXTRACTION_BASE_URL)
MATCH_TIMEOUT_SECONDS = _env_float("MATCH_TIMEOUT_SECONDS", 90.0)
MATCH_ALL_TIMEOUT_SECONDS = _env_float("MATCH_ALL_TIMEOUT_SECONDS", 300.0)
MATCH_MAX_RETRIES = _env_int("MATCH_MAX_RETRIES", 2)
FEATURE_MATCH_DECISIVE_THRESHOLD = _env_float("FEATURE_MATCH_DECISIVE_THRESHOLD", 0.6)

FOLDER_INDEX_TTL_SECONDS = _env_float("FOLDER_INDEX_TTL_SECONDS", 300.0)

WEB_SEARCH_ENABLED = _env_bool("WEB_SEARCH_ENABLED", False)
WEB_SEARCH_TIMEOUT_SECONDS = _env_float("WEB_SEARCH_TIMEOUT_SECONDS", 10.0)

MAX_PDF_UPLOAD_MB = _env_float("MAX_PDF_UPLOAD_MB", 25.0)
MAX_PDF_UPLOAD_BYTES = int(MAX_PDF_UPLOAD_MB * 1024 * 1024)

MAX_PDF_PAGES = _env_int("MAX_PDF_PAGES", 40)
PDF_RENDER_TIMEOUT_SECONDS = _env_float("PDF_RENDER_TIMEOUT_SECONDS", 60.0)

EXTRACTION_ENABLED = _env_bool("EXTRACTION_ENABLED", True)

PHOTO_CLASSIFICATION_ENABLED = _env_bool("PHOTO_CLASSIFICATION_ENABLED", True)

PDF_TEXT_LAYER_ENABLED = _env_bool("PDF_TEXT_LAYER_ENABLED", True)
PDF_TEXT_MIN_WORDS = _env_int("PDF_TEXT_MIN_WORDS", 50)

WHITE_PIXEL_THRESHOLD = _env_int("WHITE_PIXEL_THRESHOLD", 235)
SPEC_PAGE_DROP_THRESHOLD = _env_float("SPEC_PAGE_DROP_THRESHOLD", 0.5)
PHOTO_COLOUR_THRESHOLD = _env_float("PHOTO_COLOUR_THRESHOLD", 1.4)

MAX_EXCEL_UPLOAD_MB = _env_float("MAX_EXCEL_UPLOAD_MB", 10.0)
MAX_EXCEL_UPLOAD_BYTES = int(MAX_EXCEL_UPLOAD_MB * 1024 * 1024)

LOCAL_PHOTOS_ROOT = os.environ.get("LOCAL_PHOTOS_ROOT", os.path.join(_BACKEND_DIR, "data", "local_photos"))
ONEDRIVE_REQUEST_TIMEOUT_SECONDS = _env_float("ONEDRIVE_REQUEST_TIMEOUT_SECONDS", 30.0)
MAX_ONEDRIVE_PHOTOS = _env_int("MAX_ONEDRIVE_PHOTOS", 200)

PHOTO_CLASSIFIER_MODEL_PATH = os.environ.get(
    "PHOTO_CLASSIFIER_MODEL_PATH", os.path.join(_BACKEND_DIR, "app", "models", "car_interior_exterior_classifier.onnx")
)
PHOTO_CLASSIFIER_MIN_CONFIDENCE = _env_float("PHOTO_CLASSIFIER_MIN_CONFIDENCE", 0.6)


def setup_logging() -> None:
    """Configures single-line stdout logging at the LOG_LEVEL environment level, defaulting to INFO."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stdout,
    )


DESCRIPTION_PARSER_USE_AI = _env_bool("DESCRIPTION_PARSER_USE_AI", True)
DESCRIPTION_PARSER_MAX_TOKENS = _env_int("DESCRIPTION_PARSER_MAX_TOKENS", 3000)

COLOR_MATCH_USE_AI = _env_bool("COLOR_MATCH_USE_AI", True)
