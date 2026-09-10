"""FastAPI app serving the admin pages plus auto-fill, matching, Stock Sync and taxonomy APIs."""
import base64
import io
import logging
import os
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

from app import agent_graph, config
from app.db import add_listing, get_listing, load_listings, now_iso
from app.extract import (extract_from_pdf, match_all_with_llm,
                         match_features_with_llm, match_fields_with_llm)
from app.local_photos import read_bytes
from app.photo_classifier import classify as classify_photo
from app.vehicle_fields import build_vehicle_fields
from app.stock_sync import (
    apply_hidden,
    apply_price_updates,
    compute_report,
    get_pending_new_entry,
    load_pending_report,
    reconcile_with_inventory,
    remove_from_pending,
    remove_published_new,
    save_pending_report,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _find_frontend_dir():
    """Returns the frontend folder from FRONTEND_DIR or a sibling checkout, or None when there is none."""
    configured = os.environ.get("FRONTEND_DIR")
    candidates = [configured] if configured else ["../frontend", "../lisitng_automation_frontend"]
    for candidate in candidates:
        path = (BACKEND_DIR / candidate).resolve()
        if path.is_dir():
            return path
    return None


FRONTEND_DIR = _find_frontend_dir()

config.setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Legend Admin — Prototype")

FastAPIInstrumentor.instrument_app(app)


@app.middleware("http")
async def request_logging(request, call_next):
    """Logs each HTTP request's method, path, status code and latency."""
    t0 = time.monotonic()
    response = await call_next(request)
    elapsed = time.monotonic() - t0
    logger.info("%s %s -> %d (%.3fs)", request.method, request.url.path, response.status_code, elapsed)
    return response


@app.middleware("http")
async def no_cache_static_files(request, call_next):
    """Adds no-cache headers to responses for .js, .html, .css and .json paths."""
    response = await call_next(request)
    if request.url.path.endswith((".js", ".html", ".css", ".json")):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


class MatchFeaturesRequest(BaseModel):
    """Request body for feature matching: raw features, description and available checkbox labels."""
    raw_features: list[str] = []
    description: str = ""
    available_features: list[str] = []


class AvailableField(BaseModel):
    """Form field found on the page, with its id, label, input type and select options."""
    id: str
    label: str = ""
    type: str = "text"
    options: list[str] = []


class MatchFieldsRequest(BaseModel):
    """Request body for field matching: raw spec rows, description and available form fields."""
    raw_specifications: list[dict] = []
    description: str = ""
    available_fields: list[AvailableField] = []


class MatchAllRequest(BaseModel):
    """Request body for combined field and feature matching in a single call."""
    raw_specifications: list[dict] = []
    raw_features: list[str] = []
    description: str = ''
    available_fields: list[AvailableField] = []
    available_features: list[str] = []


class ListingSource(BaseModel):
    """Caller-supplied description, price and colours that take precedence over the pending report."""
    product_description: str | None = None
    price_aed: float | None = None
    exterior_color: str | None = None
    interior_color: str | None = None


class ApplyHiddenRequest(BaseModel):
    """Request body listing the model numbers to hide."""
    model_numbers: list[str]


class PriceUpdateItem(BaseModel):
    """Single price change: a model number and its new AED price."""
    model_number: str
    new_price_aed: float


class ApplyPriceUpdatesRequest(BaseModel):
    """Request body holding price update items for the price update and restock endpoints."""
    items: list[PriceUpdateItem]


THUMBNAIL_MAX_DIMENSION = 800


def _to_thumbnail_data_uri(image_bytes: bytes) -> str:
    """Resizes image bytes to fit within 800px and returns a base64 JPEG data URI."""
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((THUMBNAIL_MAX_DIMENSION, THUMBNAIL_MAX_DIMENSION), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


async def _read_validated_upload(file: UploadFile, max_bytes: int, allowed_extensions: set[str]) -> bytes:
    """Reads an uploaded file, rejecting disallowed extensions, empty files and files over the size limit."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext or '(none)'}' — expected one of {sorted(allowed_extensions)}",
        )
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content) / 1024 / 1024:.1f}MB) — max {max_bytes / 1024 / 1024:.0f}MB",
        )
    return content


@app.get("/health")
async def health():
    """Reports ok, degraded or unhealthy depending on whether the OpenRouter and Groq API keys are set."""
    checks = {
        "openrouter_api_key": bool(os.environ.get("OPENROUTER_API_KEY")),
        "groq_api_key": bool(os.environ.get("GROQ_API_KEY")),
    }
    healthy = checks["openrouter_api_key"]
    degraded = checks["openrouter_api_key"] and not checks["groq_api_key"]
    status = "ok" if healthy and not degraded else ("degraded" if healthy else "unhealthy")
    return {"status": status, "checks": checks}


@app.get("/inventory")
async def get_inventory():
    """Returns every stored listing."""
    return load_listings()


@app.post("/match-features")
async def match_features(payload: MatchFeaturesRequest):
    """Asks the LLM which feature checkboxes apply and returns matched plus suggested extra features."""
    try:
        result = await run_in_threadpool(
            match_features_with_llm, payload.raw_features, payload.description, payload.available_features
        )
        return {"features": result["matched_features"], "suggested_features": result["suggested_features"]}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/match-fields")
async def match_fields(payload: MatchFieldsRequest):
    """Asks the LLM to map the car's raw specifications onto the available form fields."""
    try:
        fields = await run_in_threadpool(
            match_fields_with_llm, payload.raw_specifications, payload.description,
            [f.model_dump() for f in payload.available_fields],
        )
        return {"fields": fields}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/match-all")
async def match_all(payload: MatchAllRequest):
    """Matches form fields and feature checkboxes in one LLM call and returns fields and features."""
    try:
        result = await run_in_threadpool(
            match_all_with_llm, payload.raw_specifications, payload.raw_features,
            payload.description, [f.model_dump() for f in payload.available_fields],
            payload.available_features,
        )
        return {"fields": result["fields"],
                "features": result["matched_features"],
                "suggested_features": result["suggested_features"]}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


def _classify_photos(listing: dict) -> dict:
    """Sorts listing photos into exterior, interior and unclassified thumbnails; empty lists if disabled."""
    if not config.PHOTO_CLASSIFICATION_ENABLED:
        logger.info("auto_fill: photo classification is switched off — skipping %d photo(s)",
                    len(listing.get("photos") or []))
        return {"exterior": [], "interior": [], "unclassified": []}
    exterior, interior, unclassified = [], [], []
    for photo in listing["photos"]:
        try:
            data = read_bytes(photo["path"])
        except OSError as e:
            logger.warning("auto_fill: couldn't read %s: %s", photo["name"], e)
            continue
        try:
            label, confidence = classify_photo(data)
        except Exception as e:
            logger.warning("auto_fill: classification failed for %s: %s", photo["name"], e)
            continue
        entry = {"name": photo["name"], "url": _to_thumbnail_data_uri(data), "confidence": round(confidence, 3)}
        (exterior if label == "exterior" else interior if label == "interior" else unclassified).append(entry)
    logger.info("auto_fill: %d exterior, %d interior, %d unclassified (from %d found)",
                len(exterior), len(interior), len(unclassified), len(listing["photos"]))
    return {"exterior": exterior, "interior": interior, "unclassified": unclassified}


def _listing_entry(model_number: str, source: "ListingSource | None") -> dict:
    """Merges a model number's pending report entry with caller-supplied values, caller values winning."""
    entry = dict(get_pending_new_entry(model_number) or {})
    if source is not None:
        if source.product_description:
            entry["product_description"] = source.product_description
        if source.price_aed is not None:
            entry["price_aed"] = source.price_aed
        if source.exterior_color:
            entry["exterior_color"] = source.exterior_color
        if source.interior_color:
            entry["interior_color"] = source.interior_color
    return entry


@app.post("/inventory/new/{model_number}/auto-fill")
async def auto_fill_new_model(model_number: str, source: ListingSource | None = None):
    """Locates a new car's folder, extracts its spec sheet, classifies its photos and returns form data."""
    entry = _listing_entry(model_number, source)
    search_key = entry.get("product_description") or model_number

    state = await run_in_threadpool(agent_graph.run_pipeline, model_number, entry)

    status = state.get("status")
    if status == "needs_folder_link":
        raise HTTPException(
            status_code=404,
            detail={"code": "pdf_not_found", "message": "; ".join(state.get("errors", []))},
        )
    if status == "needs_review":
        raise HTTPException(status_code=503, detail="; ".join(state.get("errors", [])))

    extraction = state.get("extraction")
    extraction_error = state.get("extraction_error")

    try:
        photos = await run_in_threadpool(_classify_photos, {"photos": state.get("photos", [])})
        photos_error = None
    except Exception as e:
        logger.exception("auto_fill: photo classification failed for %s", model_number)
        photos = None
        photos_error = str(e)

    if extraction is None and photos is None and config.EXTRACTION_ENABLED:
        raise HTTPException(
            status_code=502,
            detail=f"Both extraction and photo classification failed. "
                   f"Extraction: {extraction_error}. Photos: {photos_error}",
        )

    vehicle = await run_in_threadpool(build_vehicle_fields, search_key, model_number,
                                       state.get("sharepoint_folder"),
                                       entry.get("exterior_color"), entry.get("interior_color"))

    return {
        "vehicle": vehicle,
        "price_aed": entry.get("price_aed"),
        "folder_path": state.get("sharepoint_folder"),
        "extraction": extraction,
        "extraction_error": extraction_error,
        "photos": photos,
        "photos_error": photos_error,
        "spec_sheet_pdfs_found": [p["name"] for p in state.get("pdfs", [])],
    }


@app.post("/inventory/new/{model_number}/extract-upload")
async def extract_upload_new_model(model_number: str, file: UploadFile = File(...),
                                    product_description: str | None = Form(None),
                                    price_aed: float | None = Form(None)):
    """Extracts spec data from an uploaded PDF and returns it with the car's vehicle fields and price."""
    content = await _read_validated_upload(file, config.MAX_PDF_UPLOAD_BYTES, {".pdf"})
    entry = _listing_entry(model_number,
                           ListingSource(product_description=product_description, price_aed=price_aed))
    vehicle = await run_in_threadpool(
        build_vehicle_fields, entry.get("product_description") or model_number, model_number
    )

    extraction_context = {"model_number": model_number, "product_description": (entry or {}).get("product_description")}

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / file.filename
        pdf_path.write_bytes(content)
        try:
            extraction = await run_in_threadpool(extract_from_pdf, str(pdf_path), extraction_context)
            extraction_error = None
        except Exception as e:
            logger.exception("extract_upload: extraction failed for %s", model_number)
            extraction, extraction_error = None, str(e)

    return {
        "vehicle": vehicle,
        "price_aed": entry.get("price_aed"),
        "extraction": extraction,
        "extraction_error": extraction_error,
    }


@app.post("/inventory/stock-sync")
async def upload_stock_file(file: UploadFile = File(...)):
    """Compares an uploaded stock Excel against listings and saves the report as pending without DB writes."""
    content = await _read_validated_upload(file, config.MAX_EXCEL_UPLOAD_BYTES, {".xlsx", ".xls"})
    with tempfile.TemporaryDirectory() as tmp:
        excel_path = Path(tmp) / file.filename
        excel_path.write_bytes(content)
        try:
            report = await run_in_threadpool(compute_report, str(excel_path))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return save_pending_report(report, file.filename)


@app.get("/inventory/stock-sync/pending")
async def get_pending_stock_report():
    """Returns the saved Stock Sync report without rows the inventory already settles, or an empty object when none exists."""
    report = load_pending_report()
    return reconcile_with_inventory(report, load_listings()) if report is not None else {}


@app.post("/inventory/stock-sync/apply-hidden")
async def apply_hidden_endpoint(payload: ApplyHiddenRequest):
    """Hides the given listings and removes them from the pending report's hidden bucket."""
    applied = apply_hidden(payload.model_numbers)
    remaining = remove_from_pending("hidden", applied)
    logger.info("apply_hidden: %d applied", len(applied))
    return {"applied": applied, "pending": remaining}


@app.post("/inventory/stock-sync/apply-price-updates")
async def apply_price_updates_endpoint(payload: ApplyPriceUpdatesRequest):
    """Sets new prices on listings, marks them active and removes them from the pending price_updated bucket."""
    items = [item.model_dump() for item in payload.items]
    applied = apply_price_updates(items)
    remaining = remove_from_pending("price_updated", applied)
    logger.info("apply_price_updates: %d applied", len(applied))
    return {"applied": applied, "pending": remaining}


@app.post("/inventory/stock-sync/apply-restock")
async def apply_restock_endpoint(payload: ApplyPriceUpdatesRequest):
    """Reactivates listings at new prices and removes them from the pending report's restocked bucket."""
    items = [item.model_dump() for item in payload.items]
    applied = apply_price_updates(items)
    remaining = remove_from_pending("restocked", applied)
    logger.info("apply_restock: %d applied", len(applied))
    return {"applied": applied, "pending": remaining}


@app.post("/inventory/new/{model_number}/approve")
async def approve_new_model(model_number: str, payload: dict):
    """Publishes a reviewed car as an active listing, returning 409 if the ID exists, and clears it from pending New Models."""
    if get_listing(model_number):
        raise HTTPException(status_code=409, detail=f"{model_number} already exists")

    listing = {
        "model_number": model_number,
        "status": "active",
        "photos": {"exterior": ["placeholder:exterior"], "interior": ["placeholder:interior"]},
        "created_at": now_iso(),
        "updated_at": now_iso(),
        **payload,
    }
    add_listing(listing)
    remove_published_new(model_number)
    logger.info("approve_new_model: published %s", model_number)
    return listing


class ResolveTaxonomyRequest(BaseModel):
    """Request body with extracted vehicle values and the brand, model and trim catalog."""
    extracted: dict = {}
    brands: list[dict] = []


@app.post("/resolve-taxonomy")
async def resolve_taxonomy_endpoint(payload: ResolveTaxonomyRequest):
    """Resolves extracted brand, model and trim to catalog ids, each scoped to its parent."""
    from app.taxonomy import parse_taxonomy, resolve_vehicle

    brands = parse_taxonomy(payload.brands)
    if not brands:
        raise HTTPException(status_code=400, detail="`brands` was empty.")
    return resolve_vehicle(payload.extracted, brands)


class ResolveFeaturesRequest(BaseModel):
    """Request body with feature labels and the feature catalog to resolve them against."""
    labels: list[str] = []
    catalog: dict = {}


@app.post("/resolve-features")
async def resolve_features_endpoint(payload: ResolveFeaturesRequest):
    """Resolves feature labels to deduplicated catalog ids and checks mandatory feature groups."""
    from app.taxonomy import parse_feature_catalog, resolve_features

    catalog = parse_feature_catalog(payload.catalog)
    if not catalog.features:
        raise HTTPException(status_code=400, detail="`catalog.features` was empty.")
    return resolve_features(payload.labels, catalog)


class ResolveSpecsRequest(BaseModel):
    """Request body with spec rows, the specification catalog, folder path segments and model code."""
    raw_specifications: list[dict] = []
    catalog: dict = {}
    folder_segments: list[str] = []
    model_code: dict = {}


@app.post("/resolve-specifications")
async def resolve_specifications_endpoint(payload: ResolveSpecsRequest):
    """Resolves spec rows to specification value ids per parent field, using folder segments and model code."""
    from app.taxonomy import parse_spec_catalog, resolve_specifications

    catalog = parse_spec_catalog(payload.catalog)
    if not catalog.values:
        raise HTTPException(status_code=400, detail="`catalog.values` was empty.")
    return resolve_specifications(
        payload.raw_specifications, catalog,
        folder_segments=payload.folder_segments,
        model_code=payload.model_code,
    )


@app.get("/schema.json")
async def form_schema():
    """Serves the listing form definition from the backend data folder."""
    return FileResponse(BACKEND_DIR / "data" / "schema.json", media_type="application/json")


@app.get("/taxonomy.json")
async def form_taxonomy():
    """Serves the brand and model taxonomy behind the form dropdowns from the backend data folder."""
    return FileResponse(BACKEND_DIR / "data" / "taxonomy.json", media_type="application/json")


if FRONTEND_DIR is not None:
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
else:
    logger.warning("No frontend folder found; serving the API only. Set FRONTEND_DIR to a checkout of the frontend repository.")
