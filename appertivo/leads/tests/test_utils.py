"""Tests for lead utility helpers."""
from __future__ import annotations

from appertivo.leads import utils


def test_pick_city_returns_seed_city():
    """pick_city should always return one of the seeded locations."""

    cities = {utils.pick_city() for _ in range(100)}
    for city in cities:
        assert city in utils.CITIES


def test_extract_outscraper_job_id_direct_key() -> None:
    payload = {"id": "job-123", "status": "Pending"}
    assert utils.extract_outscraper_job_id(payload) == "job-123"


def test_extract_outscraper_job_id_nested_payload() -> None:
    payload = {"metadata": {"task_id": "abc-789"}, "data": []}
    assert utils.extract_outscraper_job_id(payload) == "abc-789"
