"""Utility helpers for the leads app."""
from __future__ import annotations

import random
from typing import List

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
