"""Views for the internal assets workspace."""

from __future__ import annotations

import logging
import mimetypes
import uuid
from typing import Iterable

import requests
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.files.base import ContentFile
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


def _flatten_output(output: object) -> Iterable[str]:
    """Return any string candidates from a Replicate response."""

    if output is None:
        return []

    if isinstance(output, str):
        return [output]

    if isinstance(output, dict):
        values: list[str] = []
        for value in output.values():
            values.extend(_flatten_output(value))
        return values

    if isinstance(output, (list, tuple, set)):
        values: list[str] = []
        for item in output:
            values.extend(_flatten_output(item))
        return values

    return []


def _generate_preview(model: AssetModel, prompt: str) -> tuple[str | None, str | None]:
    """Trigger Replicate and return the temporary preview URL."""

    if not replicate_client:
        return None, "Replicate API is not configured."

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
        return None, "Unable to reach Replicate right now."

    for candidate in _flatten_output(output):
        if candidate.startswith("http"):
            logger.info("Preview generated for model %s", model.identifier)
            return candidate, None

    logger.warning("Replicate output did not include a URL for model %s", model.identifier)
    return None, "Replicate did not return an image URL."


def _save_preview(*, user, model_id: int, prompt_text: str, preview_url: str) -> tuple[GeneratedAsset | None, str | None]:
    """Persist the remote preview image under MEDIA_ROOT."""

    model = get_object_or_404(AssetModel, pk=model_id)

    try:
        response = requests.get(preview_url, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:  # pragma: no cover - network guard
        logger.warning("Failed to download preview %s: %s", preview_url, exc)
        return None, "Could not download the preview image."

    content_type = response.headers.get("content-type", "image/png").split(";")[0].strip()
    extension = mimetypes.guess_extension(content_type) or ".png"
    filename = f"{slugify(model.description) or 'asset'}-{uuid.uuid4().hex}{extension}"

    asset = GeneratedAsset(
        model=model,
        prompt=prompt_text,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        preview_url=preview_url,
    )
    asset.image.save(filename, ContentFile(response.content), save=True)
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
                    preview_url, error = _generate_preview(model, prompt_text)
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
                )
                if error:
                    messages.error(request, error)
                    preview_url = save_form.cleaned_data["preview_url"]
                    preview_prompt = save_form.cleaned_data["prompt_text"]
                    selected_model_id = save_form.cleaned_data["model_id"]
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
