"""Views for the internal assets workspace."""

from __future__ import annotations

import logging
import mimetypes
import uuid
from pathlib import Path

import requests
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django_q.tasks import async_task

from app.llm import replicate_client

from .forms import (
    AssetGenerationForm,
    AssetModelForm,
    AssetSaveForm,
    PromptTemplateForm,
)
from .models import AssetModel, AssetPreviewJob, GeneratedAsset, PromptTemplate

logger = logging.getLogger(__name__)


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
            model = None
            prompt_text = ""
            if generation_form.is_valid():
                model = generation_form.cleaned_data["model"]
                template = generation_form.cleaned_data["prompt_template"]
                prompt_text = (generation_form.cleaned_data["prompt_text"] or "").strip()
                if not prompt_text and template:
                    prompt_text = template.text
                if not prompt_text:
                    generation_form.add_error(
                        "prompt_text",
                        "Provide a prompt or choose from the library.",
                    )

            if generation_form.errors:
                return JsonResponse({"errors": generation_form.errors}, status=400)

            assert model is not None  # Satisfy type checkers.
            job = AssetPreviewJob.objects.create(
                model=model,
                prompt=prompt_text,
            )
            async_task("appertivo.assets.tasks.run_preview_job", job.pk)
            return JsonResponse(
                {
                    "job_id": job.pk,
                    "status": job.status,
                    "status_url": reverse("assets:preview-status", args=[job.pk]),
                },
                status=202,
            )
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
def preview_status(request: HttpRequest, job_id: int) -> JsonResponse:
    """Return the current status for a preview generation job."""

    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    job = get_object_or_404(AssetPreviewJob, pk=job_id)
    return JsonResponse(
        {
            "id": job.pk,
            "status": job.status,
            "prompt": job.prompt,
            "model_id": job.model_id,
            "preview_url": job.preview_url,
            "storage_path": job.storage_path,
            "error": job.error_message,
        }
    )


@staff_member_required
def gallery(request: HttpRequest) -> HttpResponse:
    """Display every saved generated asset in a gallery view."""

    assets = GeneratedAsset.objects.select_related("model", "created_by").all()
    context = {
        "assets": assets,
    }
    return render(request, "assets/gallery.html", context)
