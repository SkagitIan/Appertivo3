"""Views for the internal assets workspace."""

from __future__ import annotations

import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Iterable

import requests
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.conf import settings
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from app.llm import replicate_client

from .forms import (
    AssetGenerationForm,
    AssetModelForm,
    AssetSaveForm,
    PromptTemplateForm,
)
from .models import AssetModel, GeneratedAsset, PromptTemplate

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


def _generate_preview(model: AssetModel, prompt: str) -> tuple[str | None, str | None, str | None]:
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


def _save_preview(
    *,
    user,
    model_id: int,
    prompt_text: str,
    preview_url: str,
    storage_path: str | None = None,
) -> tuple[GeneratedAsset | None, str | None]:
    """Persist the preview image under MEDIA_ROOT."""

    model = get_object_or_404(AssetModel, pk=model_id)

    file_bytes: bytes | None = None
    extension = ".png"

    if storage_path:
        try:
            with default_storage.open(storage_path, "rb") as stored_file:
                file_bytes = stored_file.read()
        except FileNotFoundError:
            logger.warning("Stored preview missing at %s", storage_path)
            return None, "Preview file is no longer available. Please generate it again."
        except OSError as exc:  # pragma: no cover - storage guard
            logger.warning("Failed to read stored preview %s: %s", storage_path, exc)
            return None, "Could not read the preview image from storage."

        suffix = Path(storage_path).suffix
        if suffix:
            extension = suffix
    else:
        try:
            response = requests.get(preview_url, timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:  # pragma: no cover - network guard
            logger.warning("Failed to download preview %s: %s", preview_url, exc)
            return None, "Could not download the preview image."

        file_bytes = response.content
        header_type = response.headers.get("content-type", "image/png").split(";")[0].strip()
        extension = mimetypes.guess_extension(header_type) or ".png"

    filename = f"{slugify(model.description) or 'asset'}-{uuid.uuid4().hex}{extension}"

    asset = GeneratedAsset(
        model=model,
        prompt=prompt_text,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        preview_url=preview_url or "",
    )
    try:
        asset.image.save(filename, ContentFile(file_bytes or b""), save=True)
    except OSError as exc:  # pragma: no cover - filesystem guard
        logger.error("Unable to save generated asset %s: %s", filename, exc, exc_info=True)
        return None, "Could not save the image to storage. Check media folder permissions."

    if storage_path:
        try:
            default_storage.delete(storage_path)
        except Exception:  # pragma: no cover - best effort cleanup
            logger.info("Temporary preview %s could not be deleted", storage_path)

    logger.info("Saved generated asset %s", asset.image.name)
    return asset, None


@staff_member_required
def dashboard(request: HttpRequest) -> HttpResponse:
    """Render the internal workspace for generating image assets."""

    model_form = AssetModelForm(prefix="model")
    prompt_form = PromptTemplateForm(prefix="prompt")
    generation_form = AssetGenerationForm(prefix="generate")
    save_form = AssetSaveForm(prefix="save")
    preview_url: str | None = None
    preview_prompt: str = ""
    selected_model_id: int | None = None
    preview_storage_path: str | None = None

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create-model":
            model_form = AssetModelForm(request.POST, prefix="model")
            if model_form.is_valid():
                model_form.save()
                messages.success(request, "Model saved. It is now available in the dropdown.")
                return redirect("assets:dashboard")
        elif action == "create-prompt":
            prompt_form = PromptTemplateForm(request.POST, prefix="prompt")
            if prompt_form.is_valid():
                prompt = prompt_form.save()
                messages.success(request, f'Prompt "{prompt.title}" added to the library.')
                return redirect("assets:dashboard")
        elif action == "generate-asset":
            generation_form = AssetGenerationForm(request.POST, prefix="generate")
            if generation_form.is_valid():
                model = generation_form.cleaned_data["model"]
                template = generation_form.cleaned_data["prompt_template"]
                prompt_text = (generation_form.cleaned_data["prompt_text"] or "").strip()
                if not prompt_text and template:
                    prompt_text = template.text
                if not prompt_text:
                    generation_form.add_error("prompt_text", "Provide a prompt or choose from the library.")
                else:
                    preview_url, preview_storage_path, error = _generate_preview(model, prompt_text)
                    preview_prompt = prompt_text
                    selected_model_id = model.pk
                    if error:
                        messages.error(request, error)
                    elif preview_url:
                        messages.success(request, "Preview ready. Save it below if you like the result.")
        elif action == "save-asset":
            save_form = AssetSaveForm(request.POST, prefix="save")
            if save_form.is_valid():
                asset, error = _save_preview(
                    user=request.user,
                    model_id=save_form.cleaned_data["model_id"],
                    prompt_text=save_form.cleaned_data["prompt_text"],
                    preview_url=save_form.cleaned_data["preview_url"],
                    storage_path=save_form.cleaned_data.get("storage_path") or None,
                )
                if error:
                    messages.error(request, error)
                    preview_url = save_form.cleaned_data["preview_url"]
                    preview_prompt = save_form.cleaned_data["prompt_text"]
                    selected_model_id = save_form.cleaned_data["model_id"]
                    preview_storage_path = save_form.cleaned_data.get("storage_path") or None
                else:
                    messages.success(request, "Image saved to the gallery.")
                    return redirect("assets:gallery")
            else:
                preview_url = save_form.data.get("save-preview_url")
                preview_prompt = save_form.data.get("save-prompt_text", "")
                try:
                    selected_model_id = int(save_form.data.get("save-model_id", ""))
                except (TypeError, ValueError):
                    selected_model_id = None
                preview_storage_path = save_form.data.get("save-storage_path") or None

    recent_assets: QuerySet[GeneratedAsset] = GeneratedAsset.objects.select_related("model").all()[:6]

    context = {
        "model_form": model_form,
        "prompt_form": prompt_form,
        "generation_form": generation_form,
        "save_form": save_form,
        "preview_url": preview_url,
        "preview_prompt": preview_prompt,
        "selected_model_id": selected_model_id,
        "recent_assets": recent_assets,
        "has_replicate": replicate_client is not None,
        "preview_storage_path": preview_storage_path,
    }
    return render(request, "assets/dashboard.html", context)


@staff_member_required
def gallery(request: HttpRequest) -> HttpResponse:
    """Display every saved generated asset in a gallery view."""

    assets = GeneratedAsset.objects.select_related("model", "created_by").all()
    context = {
        "assets": assets,
    }
    return render(request, "assets/gallery.html", context)
