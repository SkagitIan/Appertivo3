"""Background jobs for the assets application."""

from __future__ import annotations

import logging
import uuid
from typing import Iterable

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from app.llm import replicate_client

from .models import AssetPreviewJob

logger = logging.getLogger(__name__)


def _collect_preview_candidates(output: object) -> Iterable[bytes | str]:
    """Yield potential preview values from a Replicate response."""

    if output is None:
        return []

    if isinstance(output, (bytes, bytearray)):
        return [bytes(output)]

    if isinstance(output, str):
        return [output]

    if isinstance(output, dict):
        values: list[bytes | str] = []
        for value in output.values():
            values.extend(_collect_preview_candidates(value))
        return values

    if isinstance(output, (list, tuple, set)):
        values: list[bytes | str] = []
        for item in output:
            values.extend(_collect_preview_candidates(item))
        return values

    return []


def _store_preview_bytes(data: bytes) -> tuple[str | None, str | None]:
    """Persist preview bytes to storage and return the URL and storage path."""

    filename = f"previews/{uuid.uuid4().hex}.png"
    try:
        storage_path = default_storage.save(filename, ContentFile(data))
    except Exception as exc:  # pragma: no cover - storage guard
        logger.warning("Failed to store preview bytes: %s", exc, exc_info=True)
        return None, None

    try:
        url = default_storage.url(storage_path)
    except Exception:  # pragma: no cover - fallback for storages without URL support
        base_url = getattr(settings, "MEDIA_URL", "/media/").rstrip("/")
        url = f"{base_url}/{storage_path}"

    return url, storage_path


def _generate_preview(model, prompt: str) -> tuple[str | None, str | None, str | None]:
    """Trigger Replicate and return the preview URL and optional storage path."""

    if not replicate_client:
        return None, None, "Replicate API is not configured."

    try:
        output = replicate_client.run(
            model.identifier,
            input={
                "prompt": prompt,
                "output_format": "png",
                "output_quality": 95,
            },
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("Replicate call failed: %s", exc, exc_info=True)
        message = "Replicate timed out while generating the image. Please try again."
        if "504" in str(exc):
            message = "Replicate timed out before returning an image. Try again in a moment."
        return None, None, message

    for candidate in _collect_preview_candidates(output):
        if isinstance(candidate, str):
            link = candidate.strip()
            if link.startswith("http") or link.startswith("data:"):
                logger.info("Preview generated for model %s", model.identifier)
                return link, None, None
        elif isinstance(candidate, (bytes, bytearray)):
            url, storage_path = _store_preview_bytes(bytes(candidate))
            if url and storage_path:
                logger.info("Preview bytes stored for model %s", model.identifier)
                return url, storage_path, None
            return None, None, "Could not store the preview image. Check media storage permissions."

    logger.warning("Replicate output did not include a usable image for model %s", model.identifier)
    return None, None, "Replicate did not return an image URL."


def run_preview_job(job_id: int) -> None:
    """Execute the preview generation job and persist the result on the model."""

    try:
        job = AssetPreviewJob.objects.select_related("model").get(pk=job_id)
    except AssetPreviewJob.DoesNotExist:  # pragma: no cover - safety guard
        logger.warning("Preview job %s no longer exists", job_id)
        return

    job.status = AssetPreviewJob.Status.RUNNING
    job.error_message = ""
    job.save(update_fields=["status", "error_message", "updated_at"])

    try:
        preview_url, storage_path, error = _generate_preview(job.model, job.prompt)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Preview job %s failed unexpectedly: %s", job_id, exc)
        job.status = AssetPreviewJob.Status.FAILED
        job.error_message = "Unexpected error while generating the preview."
        job.save(update_fields=["status", "error_message", "updated_at"])
        return

    if error or not preview_url:
        job.status = AssetPreviewJob.Status.FAILED
        job.error_message = error or "Preview generation did not return an image."
        job.save(update_fields=["status", "error_message", "updated_at"])
        return

    job.status = AssetPreviewJob.Status.SUCCESS
    job.preview_url = preview_url
    job.storage_path = storage_path or ""
    job.save(update_fields=["status", "preview_url", "storage_path", "updated_at"])


__all__ = [
    "run_preview_job",
]
