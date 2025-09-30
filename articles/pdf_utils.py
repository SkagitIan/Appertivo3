"""Utilities for handling PDF uploads within the article workflow."""

from __future__ import annotations

import importlib.util
from typing import List

from django.core.files.uploadedfile import UploadedFile

_PYPDF_SPEC = importlib.util.find_spec("pypdf")
if _PYPDF_SPEC is not None:
    from pypdf import PdfReader  # type: ignore
else:  # pragma: no cover - falls back when dependency is missing
    PdfReader = None  # type: ignore


def extract_pdf_text(upload: UploadedFile, *, max_chars: int = 12000) -> str:
    """Return extracted text from a PDF upload truncated to ``max_chars`` characters."""

    if not upload:
        return ""

    try:
        upload.seek(0)
    except (AttributeError, OSError):  # pragma: no cover - defensive guard
        pass

    if PdfReader is None:
        return ""

    reader = PdfReader(upload)
    excerpts: List[str] = []
    current_length = 0

    for page in reader.pages:
        page_text = page.extract_text() or ""
        cleaned = page_text.strip()
        if not cleaned:
            continue
        remaining = max_chars - current_length
        if remaining <= 0:
            break
        excerpts.append(cleaned[:remaining])
        current_length += len(excerpts[-1])

    if not excerpts:
        return ""

    return "\n\n".join(excerpts)

