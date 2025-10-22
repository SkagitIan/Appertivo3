from __future__ import annotations

import json

from typing import Any, Dict, List
import re
import os
import uuid

from django.http import HttpRequest
from django.core.files.uploadedfile import UploadedFile

from .openai_helpers import calculate_nano_cost_cents


def ensure_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(data, dict):
            return data
    return {}


def ensure_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            return data
    return []


def sections_to_markdown(sections: Any) -> str:
    if not isinstance(sections, list):
        return ""
    lines: List[str] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = section.get("heading") or section.get("h2")
        if heading:
            lines.append(f"## {heading}".strip())
        paragraphs = section.get("paragraphs")
        if isinstance(paragraphs, list):
            for paragraph in paragraphs:
                if paragraph:
                    lines.append(str(paragraph).strip())
        elif section.get("body"):
            lines.append(str(section["body"]).strip())
        lines.append("")
    return "\n".join(line for line in lines if line is not None).strip()


def apply_usage_cost(run, usage: Any) -> None:
    cost_cents = calculate_nano_cost_cents(usage)
    if cost_cents:
        run.cost_cents += cost_cents
        run.save(update_fields=["cost_cents"])


def extract_ideas_from_text(text: str, *, limit: int = 5) -> List[Dict[str, str]]:
    """Best-effort extraction of idea blocks from freeform model text.

    Handles common patterns like enumerated lists with Title/Subtitle/Angle
    labels, even when markdown emphasis is present. Returns a list of idea
    dicts with keys: title, subtitle, angle.
    """

    if not text:
        return []

    # Normalize markdown emphasis to ease parsing
    cleaned = re.sub(r"[*_`]+", "", text)
    lines = [l.strip() for l in cleaned.splitlines()]

    ideas: List[Dict[str, str]] = []
    current: Dict[str, str] = {}

    def _commit():
        nonlocal current
        if current.get("title") or current.get("subtitle") or current.get("angle"):
            ideas.append({
                "title": current.get("title", "").strip(),
                "subtitle": current.get("subtitle", "").strip(),
                "angle": current.get("angle", "").strip(),
            })
        current = {}

    for raw in lines:
        if not raw:
            continue
        # Start of a new enumerated item (e.g., "1.", "2." etc.) often precedes Title
        if re.match(r"^\d+\.[ )]?", raw) and ("Title:" in raw or "TITLE:" in raw):
            _commit()
        # Try to capture labeled fields
        if ":" in raw:
            label, value = raw.split(":", 1)
            key = label.strip().lower()
            value = value.strip()
            if key.startswith("title") and value:
                if current.get("title"):
                    # A new title often signals a new idea block
                    _commit()
                current["title"] = value
                continue
            if key.startswith("subtitle"):
                current["subtitle"] = value
                continue
            if key.startswith("angle"):
                current["angle"] = value
                continue

        # If the line looks like a strong heading without label, treat as title
        if not current.get("title") and re.match(r"^\d+\.[ )]?\s*.+", raw):
            maybe = re.sub(r"^\d+\.[ )]?\s*", "", raw).strip()
            if maybe:
                current["title"] = maybe

        if len(ideas) >= limit:
            break

    _commit()

    # Trim to limit and filter empty entries
    results = [i for i in ideas if any(i.values())][:limit]
    return results


def save_pdf_upload(upload: UploadedFile | None, request: HttpRequest, *, subdir: str = "idea_pdfs") -> str:
    """Persist an uploaded PDF to storage and return an absolute URL.

    - Stores under `articles/<subdir>/<uuid>.pdf`
    - Returns an empty string if upload is None or storage/url resolution fails
    """

    if not upload:
        return ""

    # Defensive seek in case the caller consumed the stream
    try:  # pragma: no cover - depends on file type
        upload.seek(0)
    except Exception:
        pass

    # Lazy import to avoid storage binding at import time
    from django.core.files.storage import default_storage  # noqa: WPS433

    filename = f"articles/{subdir}/{uuid.uuid4().hex}.pdf"
    saved_path = default_storage.save(filename, upload)
    try:
        raw_url = default_storage.url(saved_path)
    except Exception:  # pragma: no cover - storage backends differ
        raw_url = ""
    if not raw_url:
        return ""
    if raw_url.startswith("http"):
        return raw_url
    # Build absolute URL for relative storages
    try:
        return request.build_absolute_uri(raw_url)
    except Exception:  # pragma: no cover - during tests
        return raw_url
