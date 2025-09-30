"""Utility helpers for the leads app."""
from __future__ import annotations

import random
from typing import Iterable, List, Mapping

CITIES: List[str] = [
    "New York, NY",
    "Chicago, IL",
    "San Francisco, CA",
    "Seattle, WA",
    "Portland, OR",
    "Boston, MA",
    "Washington, DC",
    "Miami, FL",
    "Buffalo, NY",
    "Providence, RI",
    "Austin, TX",
    "Nashville, TN",
    "Denver, CO",
    "New Orleans, LA",
    "Philadelphia, PA",
    "Minneapolis, MN",
    "San Diego, CA",
    "Los Angeles, CA",
    "Atlanta, GA",
    "Houston, TX",
    "Dallas, TX",
    "Las Vegas, NV",
    "Charleston, SC",
    "Madison, WI",
    "Santa Fe, NM",
]


def pick_city() -> str:
    """Return a random city from the curated list."""

    return random.choice(CITIES)


def extract_outscraper_job_id(payload: object) -> str | None:
    """Return an Outscraper job identifier from nested payload structures."""

    def _search(candidate: object) -> str | None:
        if isinstance(candidate, Mapping):
            for key in ("id", "job_id", "task_id", "request_id"):
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for key in ("metadata", "Meta", "info", "Info"):
                nested = candidate.get(key)
                if nested is not None:
                    found = _search(nested)
                    if found:
                        return found
        elif isinstance(candidate, Iterable) and not isinstance(candidate, (str, bytes)):
            for item in candidate:
                found = _search(item)
                if found:
                    return found
        return None

    return _search(payload)
