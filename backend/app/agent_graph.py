"""LangGraph pipeline that locates a car's folder, extracts its spec sheet and matches fields."""

import logging
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app import config
from app.extract import extract_from_pdfs, match_features_with_llm, match_fields_with_llm
from app.local_photos import PdfNotFoundError, list_photos_recursive
from app.stock_sync import get_pending_new_entry

logger = logging.getLogger(__name__)


class PipelineState(TypedDict, total=False):
    """Typed state passed between pipeline nodes: inputs, folder contents, extraction, matches and status."""
    model_number: str

    salesforce_record: dict | None
    sharepoint_folder: str | None
    photos: list[dict]
    pdfs: list[dict]

    extraction: dict | None
    extraction_error: str | None

    available_fields: list[dict]
    available_features: list[str]
    matched_fields: dict
    matched_features: dict

    status: str
    errors: list[str]


def salesforce_agent(state: PipelineState) -> PipelineState:
    """Loads the pending stock entry for the model number, or sets needs_review when none is staged."""
    entry = get_pending_new_entry(state["model_number"])
    if not entry:
        return {**state, "status": "needs_review",
                "errors": state.get("errors", []) + [f"no pending stock comparison entry for {state['model_number']}"]}
    return {**state, "salesforce_record": entry}


def sharepoint_agent(state: PipelineState) -> PipelineState:
    """Finds the car's folder, photos and PDFs; sets needs_folder_link if no folder or PDF, needs_review on error."""
    if state.get("status") == "needs_review":
        return state
    search_key = (state.get("salesforce_record") or {}).get("product_description") or state["model_number"]

    try:
        listing = list_photos_recursive(search_key)
    except PdfNotFoundError as e:
        return {**state, "status": "needs_folder_link", "errors": state.get("errors", []) + [str(e)]}
    except RuntimeError as e:
        return {**state, "status": "needs_review", "errors": state.get("errors", []) + [str(e)]}

    if not listing["pdfs"]:
        photo_count = len(listing.get("photos") or [])
        return {**state, "status": "needs_folder_link",
                "sharepoint_folder": listing["folder_path"],
                "photos": listing["photos"],
                "errors": state.get("errors", []) + [
                    f"Found this car's folder and {photo_count} photo(s), but it has no "
                    f"spec sheet yet — the PDF has not been uploaded to "
                    f"{listing['folder_path']}. Upload the spec sheet there, or attach it "
                    f"manually below to continue now."
                ]}

    return {**state, "sharepoint_folder": listing["folder_path"], "photos": listing["photos"], "pdfs": listing["pdfs"]}


def extraction_agent(state: PipelineState) -> PipelineState:
    """Extracts spec data from the car's PDFs when enabled, storing the result or an error without halting."""
    if state.get("status") in ("needs_review", "needs_folder_link"):
        return state

    if not config.EXTRACTION_ENABLED:
        logger.info("extraction_agent: EXTRACTION_ENABLED is off — skipping extraction for %s, "
                    "folder location only", state["model_number"])
        return {**state, "extraction": None,
                "extraction_error": "extraction is switched off (EXTRACTION_ENABLED=false) — "
                                    "folder location only"}

    search_key = (state.get("salesforce_record") or {}).get("product_description") or state["model_number"]
    context = {"model_number": state["model_number"],
               "product_description": (state.get("salesforce_record") or {}).get("product_description")}

    try:
        result = extract_from_pdfs(state.get("pdfs", []), search_key, context)
    except Exception as e:
        logger.exception("extraction_agent: extraction failed for %s", state["model_number"])
        return {**state, "extraction": None, "extraction_error": str(e)}

    return {**state, "extraction": result, "extraction_error": None}


def matching_agent(state: PipelineState) -> PipelineState:
    """Matches extracted specs and features to the available form fields and checkboxes, setting status ok."""
    if state.get("status") in ("needs_review", "needs_folder_link"):
        return state

    extraction = state.get("extraction") or {}
    matched_fields = match_fields_with_llm(
        extraction.get("raw_specifications", []), extraction.get("description", ""), state.get("available_fields", [])
    )
    matched_features = match_features_with_llm(
        extraction.get("raw_features", []), extraction.get("description", ""), state.get("available_features", [])
    )
    return {**state, "matched_fields": matched_fields, "matched_features": matched_features, "status": "ok"}


def needs_review_node(state: PipelineState) -> PipelineState:
    """Logs a needs_review warning with the collected errors and returns the state unchanged."""
    logger.warning("pipeline needs_review for %s: %s", state.get("model_number"), state.get("errors"))
    return state


def needs_folder_link_node(state: PipelineState) -> PipelineState:
    """Logs a needs_folder_link warning with the collected errors and returns the state unchanged."""
    logger.warning("pipeline needs_folder_link for %s: %s", state.get("model_number"), state.get("errors"))
    return state


def _route_after_sharepoint(state: PipelineState) -> str:
    """Returns the node to run after sharepoint_agent: needs_folder_link, needs_review or extraction_agent."""
    status = state.get("status")
    if status == "needs_folder_link":
        return "needs_folder_link"
    if status == "needs_review":
        return "needs_review"
    return "extraction_agent"


def build_graph():
    """Builds and compiles the LangGraph workflow from sharepoint_agent through extraction and matching."""
    graph = StateGraph(PipelineState)

    graph.add_node("sharepoint_agent", sharepoint_agent)
    graph.add_node("extraction_agent", extraction_agent)
    graph.add_node("matching_agent", matching_agent)
    graph.add_node("needs_review", needs_review_node)
    graph.add_node("needs_folder_link", needs_folder_link_node)

    graph.set_entry_point("sharepoint_agent")
    graph.add_conditional_edges(
        "sharepoint_agent", _route_after_sharepoint,
        {"extraction_agent": "extraction_agent", "needs_review": "needs_review", "needs_folder_link": "needs_folder_link"},
    )
    graph.add_edge("extraction_agent", "matching_agent")
    graph.add_edge("matching_agent", END)
    graph.add_edge("needs_review", END)
    graph.add_edge("needs_folder_link", END)

    return graph.compile()


def run_pipeline(
    model_number: str,
    salesforce_record: dict | None = None,
    available_fields: list[dict] = None,
    available_features: list[str] = None,
) -> PipelineState:
    """Builds the graph, seeds the initial state from the arguments and returns the final pipeline state."""
    app_graph = build_graph()
    initial_state: PipelineState = {
        "model_number": model_number,
        "salesforce_record": salesforce_record or {},
        "available_fields": available_fields or [],
        "available_features": available_features or [],
        "errors": [],
    }
    return app_graph.invoke(initial_state)
