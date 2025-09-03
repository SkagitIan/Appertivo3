"""Helpers for fetching restaurant details from Google Maps via Outscraper."""

from __future__ import annotations

import os
from typing import Dict

try:  # optional dependency at runtime
    from outscraper import ApiClient
except Exception:  # pragma: no cover
    ApiClient = None  # type: ignore

try:  # pragma: no cover - optional dependency
    from dotenv import load_dotenv
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    def load_dotenv(*args, **kwargs):
        return False

load_dotenv()


def fetch_place_details(name: str, location: str) -> Dict[str, str]:
    """Return basic place details for the first Google Maps result.

    Parameters
    ----------
    name: str
        The business name supplied by the user.
    location: str
        The location or address of the business.

    Returns
    -------
    Dict[str, str]
        A dictionary containing ``google_place_id``, ``formatted_address`` and
        ``phone_number`` keys when data is available. Returns an empty
        dictionary if the API call fails or returns no results.
    """
    if ApiClient is None:
        return {}

    api_key = os.getenv("OUTSCRAPER_API_KEY", "")
    if not api_key:
        return {}

    client = ApiClient(api_key)
    query = f"{name} {location}".strip()
    try:
        results = client.google_maps_search(query, limit=1, language="en")
    except Exception:
        return {}
    if not results:
        return {}
    place = results[0]
    return {
        "google_place_id": place.get("place_id", ""),
        "formatted_address": place.get("address", ""),
        "phone_number": place.get("phone", ""),
    }
