"""Tavily web search helper that looks up a short fact about a car brand, model and trim."""

import logging
import os

import requests

from app import config

logger = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"


def search_car_fact(brand: str, model: str, trim: str | None, question_hint: str) -> str | None:
    """Queries Tavily with brand, model, trim and a hint; returns the answer or top result text, else None."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return None

    query = f"{brand} {model} {trim or ''} {question_hint}".strip()
    try:
        response = requests.post(
            TAVILY_URL,
            json={
                "api_key": api_key, "query": query, "search_depth": "basic",
                "include_answer": True, "max_results": 3,
            },
            timeout=config.WEB_SEARCH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        logger.warning("web_search: Tavily call failed (%s), continuing without it", e)
        return None

    if data.get("answer"):
        return data["answer"]
    results = data.get("results") or []
    return results[0].get("content") if results else None
