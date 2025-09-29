"""Tests for Replicate helper utilities."""

import sys
from pathlib import Path

import django
import os

sys.path.append(str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "specials.settings")
django.setup()

from app import llm


def test_extract_output_values_handles_nested_dicts():
    output = {
        "images": ["https://example.com/image.png"],
        "meta": {"latency": 1.23},
    }

    assert llm._extract_output_values(output) == ["https://example.com/image.png"]


def test_extract_output_values_preserves_bytes_candidates():
    output = {"image": b"binary"}

    assert llm._extract_output_values(output) == [b"binary"]
