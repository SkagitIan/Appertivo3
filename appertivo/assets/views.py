"""Views for the internal assets workspace."""

from __future__ import annotations

import json
import logging
import mimetypes
import uuid
from pathlib import Path
from urllib.parse import urlparse

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
from django.views.decorators.http import require_POST
from django_q.tasks import async_task

from app.llm import client as openai_client, replicate_client
from articles.openai_helpers import extract_output_text

from .forms import (
    AssetDeleteForm,
    AssetFolderAssignmentForm,
    AssetFolderDeleteForm,
    AssetFolderForm,
    AssetFolderSecurityForm,
    AssetGenerationForm,
    AssetModelForm,
    AssetSaveForm,
    PromptTemplateForm,
)
from .models import AssetFolder, AssetModel, AssetPreviewJob, GeneratedAsset, PromptTemplate
from .tasks import refresh_preview_job

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
        preview_extension = ""
        if preview_url:
            preview_extension = Path(urlparse(preview_url).path).suffix

        candidates: list[str] = [storage_path]
        if not Path(storage_path).suffix:
            if preview_extension:
                candidate = f"{storage_path}{preview_extension}"
                if candidate not in candidates:
                    candidates.append(candidate)
            fallback_candidate = f"{storage_path}.png"
            if fallback_candidate not in candidates:
                candidates.append(fallback_candidate)

        read_path = storage_path
        for candidate in candidates:
            try:
                with default_storage.open(candidate, "rb") as stored_file:
                    file_bytes = stored_file.read()
                read_path = candidate
                break
            except FileNotFoundError:
                continue
            except OSError as exc:  # pragma: no cover - storage guard
                logger.warning("Failed to read stored preview %s: %s", candidate, exc)
                return None, "Could not read the preview image from storage."
        else:
            logger.warning("Stored preview missing at %s", storage_path)
            return None, "Preview file is no longer available. Please generate it again."

        storage_path = read_path
        suffix = Path(read_path).suffix
        if suffix:
            extension = suffix
        elif preview_extension:
            extension = preview_extension
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

    generation_form = AssetGenerationForm(prefix="generate")
    save_form = AssetSaveForm(prefix="save")
    preview_url: str | None = None
    preview_prompt: str = ""
    selected_model_id: int | None = None
    preview_storage_path: str | None = None

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "generate-asset":
            generation_form = AssetGenerationForm(request.POST, prefix="generate")
            model = None
            prompt_text = ""
            if generation_form.is_valid():
                model = generation_form.cleaned_data["model"]
                template = generation_form.cleaned_data["prompt_template"]
                additional_text = (generation_form.cleaned_data["prompt_text"] or "").strip()
                if template:
                    prompt_text = (template.text or "").strip()
                if additional_text:
                    if prompt_text:
                        prompt_text = f"{prompt_text}\n\n{additional_text}".strip()
                    else:
                        prompt_text = additional_text
                if not prompt_text:
                    generation_form.add_error(
                        "prompt_text",
                        "Provide a prompt or choose from the library.",
                    )

            if generation_form.errors:
                logger.warning(
                    "Asset preview validation failed: %s",
                    generation_form.errors,
                )
                return JsonResponse({"errors": generation_form.errors}, status=400)

            assert model is not None  # Satisfy type checkers.
            job = AssetPreviewJob.objects.create(
                model=model,
                prompt=prompt_text,
            )
            async_task(
                "appertivo.assets.tasks.run_preview_job",
                job.pk,
            )
            logger.info(
                "Queued asset preview job %s for model %s", job.pk, model.pk
            )
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

    recent_assets: QuerySet[GeneratedAsset] = (
        GeneratedAsset.objects.select_related("model", "folder")
        .exclude(folder__is_locked=True)
        [:6]
    )

    context = {
        "generation_form": generation_form,
        "save_form": save_form,
        "preview_url": preview_url,
        "preview_prompt": preview_prompt,
        "selected_model_id": selected_model_id,
        "recent_assets": recent_assets,
        "has_replicate": replicate_client is not None,
        "preview_storage_path": preview_storage_path,
        "discard_preview_url": reverse("assets:discard-preview"),
    }
    return render(request, "assets/dashboard.html", context)


@staff_member_required
def manage_models(request: HttpRequest) -> HttpResponse:
    """Create, update, or delete saved Replicate models."""

    create_form = AssetModelForm(prefix="model")
    edit_forms: dict[int, AssetModelForm] = {}

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create-model":
            create_form = AssetModelForm(request.POST, prefix="model")
            if create_form.is_valid():
                create_form.save()
                messages.success(request, "Model saved. It is now available in the studio.")
                return redirect("assets:manage-models")
        elif action == "update-model":
            try:
                model_id = int(request.POST.get("model_id", ""))
            except (TypeError, ValueError):
                messages.error(request, "Invalid model selection.")
            else:
                instance = get_object_or_404(AssetModel, pk=model_id)
                prefix = f"model-edit-{instance.pk}"
                form = AssetModelForm(request.POST, prefix=prefix, instance=instance)
                if form.is_valid():
                    form.save()
                    messages.success(request, "Model updated.")
                    return redirect("assets:manage-models")
                edit_forms[instance.pk] = form
        elif action == "delete-model":
            try:
                model_id = int(request.POST.get("model_id", ""))
            except (TypeError, ValueError):
                messages.error(request, "Invalid model selection.")
            else:
                instance = get_object_or_404(AssetModel, pk=model_id)
                instance.delete()
                messages.success(request, "Model deleted.")
                return redirect("assets:manage-models")

    models = AssetModel.objects.all()
    for model in models:
        prefix = f"model-edit-{model.pk}"
        edit_forms.setdefault(model.pk, AssetModelForm(prefix=prefix, instance=model))

    context = {
        "create_form": create_form,
        "model_entries": [(model, edit_forms[model.pk]) for model in models],
    }
    return render(request, "assets/manage_models.html", context)


@staff_member_required
def manage_prompts(request: HttpRequest) -> HttpResponse:
    """Allow staff to curate prompt templates."""

    create_form = PromptTemplateForm(prefix="prompt")
    edit_forms: dict[int, PromptTemplateForm] = {}

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create-prompt":
            create_form = PromptTemplateForm(request.POST, prefix="prompt")
            if create_form.is_valid():
                prompt = create_form.save()
                messages.success(request, f'Prompt "{prompt.title}" added to the library.')
                return redirect("assets:manage-prompts")
        elif action == "update-prompt":
            try:
                prompt_id = int(request.POST.get("prompt_id", ""))
            except (TypeError, ValueError):
                messages.error(request, "Invalid prompt selection.")
            else:
                instance = get_object_or_404(PromptTemplate, pk=prompt_id)
                prefix = f"prompt-edit-{instance.pk}"
                form = PromptTemplateForm(request.POST, prefix=prefix, instance=instance)
                if form.is_valid():
                    form.save()
                    messages.success(request, "Prompt updated.")
                    return redirect("assets:manage-prompts")
                edit_forms[instance.pk] = form
        elif action == "delete-prompt":
            try:
                prompt_id = int(request.POST.get("prompt_id", ""))
            except (TypeError, ValueError):
                messages.error(request, "Invalid prompt selection.")
            else:
                instance = get_object_or_404(PromptTemplate, pk=prompt_id)
                instance.delete()
                messages.success(request, "Prompt deleted.")
                return redirect("assets:manage-prompts")

    prompts = PromptTemplate.objects.all()
    for prompt in prompts:
        prefix = f"prompt-edit-{prompt.pk}"
        edit_forms.setdefault(prompt.pk, PromptTemplateForm(prefix=prefix, instance=prompt))

    context = {
        "create_form": create_form,
        "prompt_entries": [(prompt, edit_forms[prompt.pk]) for prompt in prompts],
    }
    return render(request, "assets/manage_prompts.html", context)


@staff_member_required
@require_POST
def enhance_prompt(request: HttpRequest) -> JsonResponse:
    """Send prompt text to OpenAI for a richer version."""

    if openai_client is None:
        return JsonResponse({"error": "LLM client is not configured."}, status=503)

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}

    text = (payload.get("text") or "").strip()
    if not text:
        return JsonResponse({"error": "Add some prompt text first."}, status=400)

    instructions = (
        "You are refining an image generation prompt for a food photography model. "
        "Rewrite the prompt with vivid scene details, plating, lighting, mood, and camera cues. "
        "Keep it under 80 words and avoid repeating the words 'prompt' or 'rewrite'. "
        "Return only the improved prompt text.\n\n"
        f"Original prompt:\n{text}"
    )

    try:
        response = openai_client.responses.create(
            model="gpt-4.1-nano",
            input=instructions,
        )
    except Exception as exc:  # pragma: no cover - network or client error
        logger.warning("Prompt enhancement failed: %s", exc, exc_info=True)
        return JsonResponse({"error": "We could not reach the enhancement service."}, status=502)

    enhanced = extract_output_text(response).strip()
    if not enhanced:
        return JsonResponse({"error": "The model did not return any text."}, status=502)

    return JsonResponse({"enhanced_text": enhanced})


@staff_member_required
def preview_status(request: HttpRequest, job_id: int) -> JsonResponse:
    """Return the current status for a preview generation job."""

    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    job = get_object_or_404(AssetPreviewJob, pk=job_id)
    if job.status not in {
        AssetPreviewJob.Status.SUCCESS,
        AssetPreviewJob.Status.FAILED,
    }:
        job = refresh_preview_job(job)
    logger.info(
        "Preview status requested for job %s (%s)", job.pk, job.status
    )
    return JsonResponse(
        {
            "id": job.pk,
            "status": job.status,
            "replicate_status": job.replicate_status,
            "prediction_id": job.prediction_id,
            "prompt": job.prompt,
            "model_id": job.model_id,
            "preview_url": job.preview_url,
            "storage_path": job.storage_path,
            "error": job.error_message,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }
    )


@staff_member_required
def gallery(request: HttpRequest) -> HttpResponse:
    """Display every saved generated asset in a gallery view."""

    assets = GeneratedAsset.objects.select_related("model", "created_by", "folder").all()
    folders = AssetFolder.objects.all()
    folder_form = AssetFolderForm(prefix="folder")
    folder_security_forms: dict[int, AssetFolderSecurityForm] = {}
    unlocked_folder_ids = {
        int(folder_id)
        for folder_id in request.session.get("asset_unlocked_folders", [])
        if isinstance(folder_id, int) or str(folder_id).isdigit()
    }
    for folder in folders:
        prefix = f"folder-security-{folder.pk}"
        folder_security_forms[folder.pk] = AssetFolderSecurityForm(prefix=prefix, instance=folder)

    selected_filter = request.GET.get("folder", "all").strip()
    selected_folder_id: int | None = None
    if selected_filter == "unassigned":
        assets = assets.filter(folder__isnull=True)
    elif selected_filter not in {"", "all"}:
        try:
            selected_folder_id = int(selected_filter)
        except (TypeError, ValueError):
            selected_filter = "all"
        else:
            assets = assets.filter(folder_id=selected_folder_id)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create-folder":
            folder_form = AssetFolderForm(request.POST, prefix="folder")
            if folder_form.is_valid():
                folder = folder_form.save()
                messages.success(request, f'Folder "{folder.name}" created.')
                return redirect("assets:gallery")
        elif action == "delete-folder":
            delete_form = AssetFolderDeleteForm(request.POST)
            if delete_form.is_valid():
                folder = get_object_or_404(AssetFolder, pk=delete_form.cleaned_data["folder_id"])
                folder_name = folder.name
                folder.delete()
                messages.success(request, f'Folder "{folder_name}" deleted.')
                return redirect("assets:gallery")
        elif action == "assign-folder":
            assign_form = AssetFolderAssignmentForm(request.POST)
            if assign_form.is_valid():
                asset = get_object_or_404(GeneratedAsset, pk=assign_form.cleaned_data["asset_id"])
                folder_id = assign_form.cleaned_data.get("folder_id")
                folder = None
                if folder_id:
                    folder = get_object_or_404(AssetFolder, pk=folder_id)
                asset.folder = folder
                asset.save(update_fields=["folder"])
                if folder:
                    messages.success(request, "Asset assigned to folder.")
                else:
                    messages.success(request, "Asset removed from its folder.")
                return redirect("assets:gallery")
        elif action == "update-folder-security":
            try:
                folder_id = int(request.POST.get("folder_id", ""))
            except (TypeError, ValueError):
                messages.error(request, "Invalid folder selection.")
            else:
                folder = get_object_or_404(AssetFolder, pk=folder_id)
                prefix = f"folder-security-{folder.pk}"
                form = AssetFolderSecurityForm(request.POST, prefix=prefix, instance=folder)
                if form.is_valid():
                    form.save()
                    messages.success(request, f'Folder "{folder.name}" updated.')
                    return redirect("assets:gallery")
                folder_security_forms[folder.pk] = form
        elif action == "delete-asset":
            asset_delete_form = AssetDeleteForm(request.POST)
            if asset_delete_form.is_valid():
                asset = get_object_or_404(GeneratedAsset, pk=asset_delete_form.cleaned_data["asset_id"])
                filename = asset.filename()
                if asset.image:
                    asset.image.delete(save=False)
                asset.delete()
                if filename:
                    messages.success(request, f"Deleted {filename}.")
                else:
                    messages.success(request, "Deleted the selected asset.")
                return redirect("assets:gallery")

    folder_entries = [
        {
            "folder": folder,
            "form": folder_security_forms[folder.pk],
            "is_unlocked": folder.pk in unlocked_folder_ids,
        }
        for folder in folders
    ]

    context = {
        "assets": assets,
        "folders": folders,
        "folder_form": folder_form,
        "folder_entries": folder_entries,
        "selected_filter": selected_filter,
        "selected_folder_id": selected_folder_id,
        "unlocked_folder_ids": sorted(unlocked_folder_ids),
    }
    return render(request, "assets/gallery.html", context)


@staff_member_required
@require_POST
def verify_folder_pin(request: HttpRequest, folder_id: int) -> JsonResponse:
    """Confirm a folder PIN before revealing protected assets."""

    folder = get_object_or_404(AssetFolder, pk=folder_id)
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        payload = request.POST

    pin = (payload.get("pin") or "").strip()

    if folder.is_locked and not pin:
        logger.warning(
            "Folder %s PIN missing for user %s",
            folder.pk,
            getattr(request.user, "pk", None),
        )
        return JsonResponse({"status": "error", "message": "Enter the folder PIN."}, status=400)

    if folder.is_locked and pin != folder.pin:
        logger.warning(
            "Folder %s PIN mismatch for user %s",
            folder.pk,
            getattr(request.user, "pk", None),
        )
        return JsonResponse({"status": "error", "message": "Incorrect PIN."}, status=400)

    unlocked = set(request.session.get("asset_unlocked_folders", []))
    unlocked.add(folder.pk)
    request.session["asset_unlocked_folders"] = list(unlocked)

    logger.info(
        "Folder %s unlocked by user %s",
        folder.pk,
        getattr(request.user, "pk", None),
    )
    return JsonResponse({"status": "ok"})


@staff_member_required
@require_POST
def discard_preview(request: HttpRequest) -> JsonResponse:
    """Delete a stored preview file after a rejection."""

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        payload = request.POST

    storage_path = (payload.get("storage_path") or "").strip()
    if not storage_path:
        logger.info("Discard preview skipped: missing path")
        return JsonResponse({"status": "skipped"})

    if ".." in storage_path or storage_path.startswith("/"):
        logger.warning("Discard preview rejected for unsafe path: %s", storage_path)
        return JsonResponse({"status": "invalid"}, status=400)

    if not storage_path.startswith("previews/"):
        logger.warning(
            "Discard preview rejected for unexpected prefix: %s", storage_path
        )
        return JsonResponse({"status": "invalid"}, status=400)

    try:
        default_storage.delete(storage_path)
    except Exception:  # pragma: no cover - storage guard
        logger.error("Failed to delete preview %s", storage_path, exc_info=True)
        return JsonResponse({"status": "error"}, status=500)

    logger.info("Discarded preview %s", storage_path)
    return JsonResponse({"status": "deleted"})
