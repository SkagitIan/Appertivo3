"""Helpers for fetching restaurant details from Google Maps via Outscraper."""

from __future__ import annotations

import os
from typing import Dict

try:  # optional dependency at runtime
    from outscraper import ApiClient
except Exception:  # pragma: no cover
    ApiClient = None  # type: ignore

from dotenv import load_dotenv
load_dotenv()
import logging
logger = logging.getLogger(__name__)
import json

def fetch_place_details(name: str, location: str) -> Dict[str, str]:
    """Return useful place details for the first Google Maps result."""
    if ApiClient is None:
        logger.warning("ApiClient not available")
        return {}

    api_key = os.getenv("OUTSCRAPER_API_KEY", "")
    if not api_key:
        logger.error("OUTSCRAPER_API_KEY not found in environment")
        return {}

    client = ApiClient(api_key)
    query = f"{name} {location}".strip()
    logger.info("Searching Google Maps for query='%s'", query)

    try:
        results = client.google_maps_search(query, limit=1, language="en")
    except Exception as e:
        logger.exception("Outscraper API call failed: %s", e)
        return {}

    if not results:
        logger.warning("No results returned for query='%s'", query)
        return {}

    # unwrap nested list
    place = results[0][0] if results and isinstance(results[0], list) and results[0] else None
    if not place or not isinstance(place, dict):
        logger.error("Unexpected result format for query='%s': %s", query, results)
        return {}

    logger.debug("Fetched place details: %s", place.get("name"))

    return {
        "google_place_id": place.get("place_id", ""),
        "formatted_address": place.get("full_address", ""),
        "phone_number": place.get("phone", ""),
        "website": place.get("site", ""),
        "category": place.get("category", ""),
        "rating": str(place.get("rating", "")),
        "reviews": str(place.get("reviews", "")),
        "photo_url": place.get("photo", ""),
        "latitude": str(place.get("latitude", "")),
        "longitude": str(place.get("longitude", "")),
        "hours": place.get("working_hours", {}),
    }