"""Thin wrappers around Outscraper HTTP APIs used during onboarding."""

from __future__ import annotations
"""Outscraper API helpers for onboarding pipeline."""

import logging
import os
import time
from typing import Any, Dict, List

import requests
from django.conf import settings

from app import models

logger = logging.getLogger(__name__)

OUTSCRAPER_SEARCH_URL = "https://api.app.outscraper.com/maps/search-v3"
OUTSCRAPER_REVIEWS_URL = "https://api.app.outscraper.com/maps/reviews-v3"
_TIMEOUT_SECONDS = 12


def _api_key() -> str:
    """Return the Outscraper API key from settings or environment."""

    return getattr(settings, "OUTSCRAPER_API_KEY", "") or os.getenv("OUTSCRAPER_API_KEY", "")


def _log_external_call(provider: str, function_name: str, metadata: Dict[str, Any]) -> None:
    """Persist a lightweight entry describing an external API call."""

    try:
        models.LlmCallLog.objects.create(
            provider=provider,
            model_name="http",
            call_type=models.LlmCallLog.CallType.TEXT,
            step="onboarding",
            function_name=function_name,
            metadata=metadata,
        )
    except Exception:  # pragma: no cover - logging failures should not break flow
        logger.exception("Unable to persist external call log", exc_info=True)


def fetch_context(onboarding: models.Onboarding) -> Dict[str, Any]:
    """Fetch Outscraper context for the onboarding restaurant."""

    api_key = _api_key()
    if not api_key:
        logger.info("OUTSCRAPER_API_KEY missing; skipping context fetch")
        return {}

    restaurant = onboarding.restaurant
    if not restaurant:
        logger.warning("Onboarding %s has no restaurant linked", onboarding.id)
        return {}

    query = f"{restaurant.name} {restaurant.location_text}".strip()
    if not query:
        return {}

    params = {
        "query": query,
        "async": "false",
        "limit": 1,
        "language": "en",
        "fields": (
            "query,name,place_id,full_address,latitude,longitude,site,phone,type," "description,category,subtypes,about,menu_link,order_links"
        ),
    }

    headers = {"X-API-KEY": api_key}
    started = time.monotonic()
    try:
        response = requests.get(OUTSCRAPER_SEARCH_URL, params=params, headers=headers, timeout=_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        metadata = {
            "params": params,
            "status_code": getattr(exc.response, "status_code", None),
            "error": str(exc),
        }
        _log_external_call("outscraper", "fetch_context", metadata)
        raise

    latency_ms = int((time.monotonic() - started) * 1000)
    data: Dict[str, Any] = {}
    payload = response.json() if response.content else {}
    entries = []
    if isinstance(payload, dict):
        entries = payload.get("data") or []
        if entries and isinstance(entries[0], list):
            entries = entries[0]
        if entries and isinstance(entries[0], dict):
            data = entries[0]

    metadata = {
        "params": params,
        "status_code": response.status_code,
        "latency_ms": latency_ms,
        "result_keys": sorted(data.keys()) if isinstance(data, dict) else [],
    }
    _log_external_call("outscraper", "fetch_context", metadata)
    return data if isinstance(data, dict) else {}


def fetch_reviews(place_id: str) -> List[Dict[str, Any]]:
    """Fetch Outscraper reviews for a Google place id."""

    api_key = _api_key()
    if not api_key or not place_id:
        if not api_key:
            logger.info("OUTSCRAPER_API_KEY missing; skipping review fetch")
        return []

    params = {
        "place_id": place_id,
        "limit": 100,
        "language": "en",
        "reviews_limit": 100,
    }
    headers = {"X-API-KEY": api_key}
    started = time.monotonic()
    try:
        response = requests.get(OUTSCRAPER_REVIEWS_URL, params=params, headers=headers, timeout=_TIMEOUT_SECONDS)
        if response.status_code == 404:
            _log_external_call(
                "outscraper",
                "fetch_reviews",
                {
                    "place_id": place_id,
                    "status_code": response.status_code,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                },
            )
            return []
        response.raise_for_status()
    except requests.RequestException as exc:
        metadata = {
            "place_id": place_id,
            "status_code": getattr(exc.response, "status_code", None),
            "error": str(exc),
        }
        _log_external_call("outscraper", "fetch_reviews", metadata)
        raise

    latency_ms = int((time.monotonic() - started) * 1000)
    payload = response.json() if response.content else {}
    reviews: List[Dict[str, Any]] = []
    if isinstance(payload, dict):
        entries = payload.get("data") or []
        if entries and isinstance(entries[0], list):
            entries = entries[0]
        if isinstance(entries, list):
            reviews = [item for item in entries if isinstance(item, dict)]

    _log_external_call(
        "outscraper",
        "fetch_reviews",
        {
            "place_id": place_id,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
            "count": len(reviews),
        },
    )
    return reviews
