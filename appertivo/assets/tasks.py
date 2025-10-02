"""Background jobs for the assets application."""

from __future__ import annotations

import logging
import uuid
from typing import Iterable

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from django.utils.functional import empty

from app.llm import replicate_client

from .models import AssetPreviewJob

logger = logging.getLogger(__name__)


def _storage_backend_name() -> str:
    """Return a descriptive name for the active default storage backend."""

    storage_obj = getattr(default_storage, "_wrapped", empty)
    if storage_obj is empty:  # pragma: no cover - defensive lazy storage setup
        default_storage._setup()  # type: ignore[attr-defined]
        storage_obj = getattr(default_storage, "_wrapped", None)

    if not storage_obj:
        return "configured storage"

    storage_class = storage_obj.__class__
    return f"{storage_class.__module__}.{storage_class.__name__}"


def _collect_preview_candidates(output: object) -> Iterable[bytes | str]:
    """Yield potential preview values from a Replicate response."""

    if output is None:
        return []

    if hasattr(output, "read"):
        try:
            return [bytes(output.read())]
        except Exception:  # pragma: no cover - defensive guard
            logger.warning("Failed to read file-like Replicate output", exc_info=True)
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
        storage_label = _storage_backend_name()
        if "cloudinary" in storage_label.lower():
            logger.warning(
                "Failed to store preview bytes via Cloudinary storage: %s",
                exc,
                exc_info=True,
            )
        else:
            logger.warning(
                "Failed to store preview bytes via %s: %s",
                storage_label,
                exc,
                exc_info=True,
            )
        return None, None

    try:
        url = default_storage.url(storage_path)
    except Exception:  # pragma: no cover - fallback for storages without URL support
        base_url = getattr(settings, "MEDIA_URL", "/media/").rstrip("/")
        url = f"{base_url}/{storage_path}"

    return url, storage_path


def _extract_prediction_error(prediction) -> str:
    """Return the most helpful error message from a Replicate prediction."""

    for attr in ("error", "logs", "status", "detail"):
        value = getattr(prediction, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Replicate did not return an image."


def _collect_preview_from_prediction(prediction) -> tuple[str | None, str | None, str | None]:
    """Extract preview data from a Replicate prediction output."""

    output = getattr(prediction, "output", None)
    for candidate in _collect_preview_candidates(output):
        if isinstance(candidate, str):
            link = candidate.strip()
            if link.startswith("http") or link.startswith("data:"):
                logger.info("Preview generated for prediction %s", getattr(prediction, "id", "?"))
                return link, None, None
        elif isinstance(candidate, (bytes, bytearray)):
            url, storage_path = _store_preview_bytes(bytes(candidate))
            if url and storage_path:
                logger.info("Preview bytes stored for prediction %s", getattr(prediction, "id", "?"))
                return url, storage_path, None
            return None, None, "Could not store the preview image. Check media storage permissions."

    logger.warning(
        "Replicate output did not include a usable image for prediction %s",
        getattr(prediction, "id", "?"),
    )
    return None, None, "Replicate did not return an image URL."


def _synchronize_job_with_prediction(
    job: AssetPreviewJob,
    prediction,
) -> AssetPreviewJob:
    """Update the preview job to reflect the latest Replicate prediction status."""

    status = (getattr(prediction, "status", "") or "").lower()
    job.replicate_status = status

    if status == "succeeded":
        preview_url, storage_path, error = _collect_preview_from_prediction(prediction)
        if error or not preview_url:
            job.status = AssetPreviewJob.Status.FAILED
            job.error_message = error or "Preview generation did not return an image."
            job.completed_at = timezone.now()
            job.save(
                update_fields=[
                    "status",
                    "error_message",
                    "replicate_status",
                    "completed_at",
                    "updated_at",
                ]
            )
            return job

        job.status = AssetPreviewJob.Status.SUCCESS
        job.preview_url = preview_url
        job.storage_path = storage_path or ""
        job.error_message = ""
        job.completed_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "preview_url",
                "storage_path",
                "error_message",
                "replicate_status",
                "completed_at",
                "updated_at",
            ]
        )
        return job

    if status in {"failed", "canceled"}:
        job.status = AssetPreviewJob.Status.FAILED
        job.error_message = _extract_prediction_error(prediction)
        job.completed_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "error_message",
                "replicate_status",
                "completed_at",
                "updated_at",
            ]
        )
        return job

    # Status is still in progress.
    job.status = AssetPreviewJob.Status.RUNNING
    job.save(update_fields=["status", "replicate_status", "updated_at"])
    return job


def refresh_preview_job(job: AssetPreviewJob) -> AssetPreviewJob:
    """Reload Replicate status for the given job and persist the result."""

    if not job.prediction_id:
        return job

    if not replicate_client:
        job.status = AssetPreviewJob.Status.FAILED
        job.replicate_status = "failed"
        job.error_message = job.error_message or "Replicate API is not configured."
        job.completed_at = job.completed_at or timezone.now()
        job.save(
            update_fields=[
                "status",
                "replicate_status",
                "error_message",
                "completed_at",
                "updated_at",
            ]
        )
        return job

    try:
        prediction = replicate_client.predictions.get(job.prediction_id)
    except Exception as exc:  # pragma: no cover - network guard
        logger.warning("Replicate prediction refresh failed for %s: %s", job.pk, exc, exc_info=True)
        job.status = AssetPreviewJob.Status.FAILED
        job.replicate_status = "failed"
        if not job.error_message:
            job.error_message = "Could not refresh the preview status from Replicate."
        job.completed_at = job.completed_at or timezone.now()
        job.save(
            update_fields=[
                "status",
                "replicate_status",
                "error_message",
                "completed_at",
                "updated_at",
            ]
        )
        return job

    return _synchronize_job_with_prediction(job, prediction)


def run_preview_job(job_id: int) -> None:
    """Execute the preview generation job and persist the result on the model."""

    try:
        job = AssetPreviewJob.objects.select_related("model").get(pk=job_id)
    except AssetPreviewJob.DoesNotExist:  # pragma: no cover - safety guard
        logger.warning("Preview job %s no longer exists", job_id)
        return

    job.status = AssetPreviewJob.Status.RUNNING
    job.error_message = ""
    job.completed_at = None
    job.replicate_status = ""
    job.save(
        update_fields=[
            "status",
            "error_message",
            "completed_at",
            "replicate_status",
            "updated_at",
        ]
    )

    if not replicate_client:
        job.status = AssetPreviewJob.Status.FAILED
        job.replicate_status = "failed"
        job.error_message = "Replicate API is not configured."
        job.completed_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "replicate_status",
                "error_message",
                "completed_at",
                "updated_at",
            ]
        )
        return

    try:
        prediction = replicate_client.predictions.create(
            version=job.model.identifier,
            input={
                "prompt": job.prompt,
                "output_format": "png",
                "output_quality": 95,
            },
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("Replicate call failed: %s", exc, exc_info=True)
        message = "Replicate timed out while generating the image. Please try again."
        if "504" in str(exc):
            message = "Replicate timed out before returning an image. Try again in a moment."
        job.status = AssetPreviewJob.Status.FAILED
        job.replicate_status = "failed"
        job.error_message = message
        job.completed_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "replicate_status",
                "error_message",
                "completed_at",
                "updated_at",
            ]
        )
        return

    job.prediction_id = getattr(prediction, "id", "") or ""
    job.save(update_fields=["prediction_id", "updated_at"])

    _synchronize_job_with_prediction(job, prediction)


__all__ = [
    "refresh_preview_job",
    "run_preview_job",
]
