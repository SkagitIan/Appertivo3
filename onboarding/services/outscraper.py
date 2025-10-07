"""Thin wrappers around Outscraper HTTP APIs used during onboarding."""

from __future__ import annotations
"""Outscraper API helpers for onboarding pipeline."""

import logging
import os
import time
from typing import Any, Dict, List

import requests
from django.conf import settings
from dotenv import load_dotenv
load_dotenv()
from app import models

logger = logging.getLogger(__name__)

OUTSCRAPER_SEARCH_URL = "https://api.app.outscraper.com/maps/search-v3"
OUTSCRAPER_REVIEWS_URL = "https://api.outscraper.cloud/google-maps-reviews"
_TIMEOUT_SECONDS = 60
_MAX_HTTP_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 0.75


def _api_key() -> str:
    """Return the Outscraper API key from settings or environment."""

    return os.getenv("OUTSCRAPER_API_KEY")


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


def _outscraper_get(
    url: str,
    *,
    params: Dict[str, Any],
    headers: Dict[str, str],
    timeout: int,
    call_name: str,
) -> requests.Response:
    """Execute an Outscraper GET request with basic retry + logging."""

    sanitized_headers = dict(headers)
    sanitized_headers.setdefault("Connection", "close")
    metadata = {"url": url, "params": params, "timeout": timeout}

    last_exc: Exception | None = None
    for attempt in range(1, _MAX_HTTP_RETRIES + 1):
        attempt_meta = {**metadata, "attempt": attempt}
        logger.info("Outscraper %s request", call_name, extra={"outscraper": attempt_meta})
        try:
            response = requests.get(
                url,
                params=params,
                headers=sanitized_headers,
                timeout=timeout,
            )
            logger.info(
                "Outscraper %s response",
                call_name,
                extra={
                    "outscraper": {
                        **attempt_meta,
                        "status_code": response.status_code,
                        "content_length": len(response.content or b"") if response.content else 0,
                    }
                },
            )
            return response
        except requests.exceptions.ConnectionError as exc:
            last_exc = exc
            logger.warning(
                "Outscraper %s connection error",
                call_name,
                exc_info=True,
                extra={"outscraper": {**attempt_meta, "error": str(exc)}},
            )
            if attempt < _MAX_HTTP_RETRIES:
                backoff = min(_RETRY_BACKOFF_SECONDS * attempt, 3)
                time.sleep(backoff)
        except requests.RequestException as exc:
            # Non-connection errors should surface immediately.
            logger.warning(
                "Outscraper %s request exception",
                call_name,
                exc_info=True,
                extra={"outscraper": {**attempt_meta, "error": str(exc)}},
            )
            raise

    assert last_exc is not None  # for type checking
    raise last_exc


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
        response = _outscraper_get(
            OUTSCRAPER_SEARCH_URL,
            params=params,
            headers=headers,
            timeout=_TIMEOUT_SECONDS,
            call_name="maps-search-v3",
        )
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
        "query": place_id,
        "language": "en",
        "reviews_limit": 1,
        "sort":"newest",
        "ignore_empty": "true",
        "async":"false",
        "fields":"placed_id,reviews_data.review_text",
    }
    headers = {"X-API-KEY": api_key}
    started = time.monotonic()
    try:
        response = _outscraper_get(
            OUTSCRAPER_REVIEWS_URL,
            params=params,
            headers=headers,
            timeout=_TIMEOUT_SECONDS,
            call_name="google-maps-reviews",
        )
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
