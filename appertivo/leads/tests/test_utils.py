"""Tests for lead utility helpers."""
from __future__ import annotations

from appertivo.leads import utils


def test_pick_city_returns_seed_city():
    """pick_city should always return one of the seeded locations."""

    cities = {utils.pick_city() for _ in range(100)}
    for city in cities:
        assert city in utils.CITIES
