"""Tests for the internal assets workspace views."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import django
from django_q.tasks import async_task as queue_async_task
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import Storage, default_storage
from django.test import TestCase
from django.urls import reverse
from django.utils.deconstruct import deconstructible

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "specials.settings")
django.setup()

from appertivo.assets import tasks
from appertivo.assets.models import AssetFolder, AssetModel, AssetPreviewJob, GeneratedAsset, PromptTemplate


@deconstructible
class _BaseMemoryStorage(Storage):
    """A minimal in-memory storage backend for tests."""

    drop_extension: bool = False

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._files: dict[str, bytes] = {}
        self._aliases: dict[str, str] = {}

    def _normalize(self, name: str) -> str:
        return str(name).replace("\\", "/")

    def _canonical_name(self, name: str) -> str:
        normalized = self._normalize(name)
        return self._aliases.get(normalized, normalized)

    def _save(self, name: str, content) -> str:
        normalized = self._normalize(name)
        if hasattr(content, "chunks"):
            data = b"".join(bytes(chunk) for chunk in content.chunks())
        else:
            raw = content.read()
            if isinstance(raw, str):
                data = raw.encode()
            elif isinstance(raw, (bytes, bytearray)):
                data = bytes(raw)
            else:
                data = bytes(raw or b"")

        base, _ = os.path.splitext(normalized)
        canonical = base if self.drop_extension else normalized
        self._files[canonical] = data
        if canonical != normalized:
            self._aliases[normalized] = canonical
        return canonical

    def _open(self, name: str, mode: str = "rb"):
        canonical = self._canonical_name(name)
        if canonical not in self._files:
            base, _ = os.path.splitext(canonical)
            canonical = base
        if canonical not in self._files:
            raise FileNotFoundError(name)
        return ContentFile(self._files[canonical], name=canonical)

    def exists(self, name: str) -> bool:  # noqa: D401 - short helper
        canonical = self._canonical_name(name)
        if canonical in self._files:
            return True
        base, _ = os.path.splitext(canonical)
        return base in self._files

    def delete(self, name: str) -> None:
        canonical = self._canonical_name(name)
        self._files.pop(canonical, None)
        aliases_to_remove = [alias for alias, target in self._aliases.items() if target == canonical]
        for alias in aliases_to_remove:
            self._aliases.pop(alias, None)
        base, _ = os.path.splitext(canonical)
        self._files.pop(base, None)

    def url(self, name: str) -> str:
        canonical = self._canonical_name(name).lstrip("/")
        return f"https://storage.test/{canonical}"

    def clear(self) -> None:
        """Reset stored files between tests."""

        self._files.clear()
        self._aliases.clear()


class CloudMemoryStorage(_BaseMemoryStorage):
    """Storage that mimics Cloudinary public IDs by omitting file extensions."""

    drop_extension = True


class AssetDashboardTests(TestCase):
    """Exercise staff-only workflows for the asset studio."""

    def setUp(self) -> None:
        super().setUp()
        original_storage = getattr(default_storage, "_wrapped", None)
        self.addCleanup(lambda: setattr(default_storage, "_wrapped", original_storage))

        storage_backend = CloudMemoryStorage()
        default_storage._wrapped = storage_backend
        self.storage = storage_backend

        user_model = get_user_model()
        self.staff_user = user_model.objects.create_user(
            username="staff", email="staff@example.com", password="pass123", is_staff=True
        )
        self.regular_user = user_model.objects.create_user(
            username="regular", email="regular@example.com", password="pass123"
        )
        self.model = AssetModel.objects.create(
            description="Flux dev",
            identifier="owner/model:123",
        )

    def test_staff_access_required(self) -> None:
        """Only staff members may open the dashboard."""

        url = reverse("assets:dashboard")
        # Anonymous users are redirected to the admin login
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.regular_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.staff_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "assets/dashboard.html")

    def test_manage_models_crud(self) -> None:
        """Staff can create, update, and delete models from the management page."""

        self.client.force_login(self.staff_user)
        create_response = self.client.post(
            reverse("assets:manage-models"),
            {
                "action": "create-model",
                "model-description": "High fidelity food",
                "model-identifier": "owner/food:456",
            },
        )
        self.assertEqual(create_response.status_code, 302)
        model = AssetModel.objects.get(identifier="owner/food:456")

        update_response = self.client.post(
            reverse("assets:manage-models"),
            {
                "action": "update-model",
                "model_id": model.pk,
                f"model-edit-{model.pk}-description": "Updated",
                f"model-edit-{model.pk}-identifier": "owner/new:789",
            },
        )
        self.assertEqual(update_response.status_code, 302)
        model.refresh_from_db()
        self.assertEqual(model.description, "Updated")
        self.assertEqual(model.identifier, "owner/new:789")

        delete_response = self.client.post(
            reverse("assets:manage-models"),
            {"action": "delete-model", "model_id": model.pk},
        )
        self.assertEqual(delete_response.status_code, 302)
        self.assertFalse(AssetModel.objects.filter(pk=model.pk).exists())

    def test_manage_prompts_crud(self) -> None:
        """Prompt templates are maintained from the dedicated page."""

        self.client.force_login(self.staff_user)
        create_response = self.client.post(
            reverse("assets:manage-prompts"),
            {
                "action": "create-prompt",
                "prompt-title": "Hero shot",
                "prompt-text": "Stunning hero image",
            },
        )
        self.assertEqual(create_response.status_code, 302)
        prompt = PromptTemplate.objects.get(title="Hero shot")

        update_response = self.client.post(
            reverse("assets:manage-prompts"),
            {
                "action": "update-prompt",
                "prompt_id": prompt.pk,
                f"prompt-edit-{prompt.pk}-title": "Updated title",
                f"prompt-edit-{prompt.pk}-text": "Updated text",
            },
        )
        self.assertEqual(update_response.status_code, 302)
        prompt.refresh_from_db()
        self.assertEqual(prompt.title, "Updated title")
        self.assertEqual(prompt.text, "Updated text")

        delete_response = self.client.post(
            reverse("assets:manage-prompts"),
            {"action": "delete-prompt", "prompt_id": prompt.pk},
        )
        self.assertEqual(delete_response.status_code, 302)
        self.assertFalse(PromptTemplate.objects.filter(pk=prompt.pk).exists())

    @patch("appertivo.assets.views.openai_client")
    def test_enhance_prompt_endpoint(self, mock_client) -> None:
        """Prompt enhancement returns richer copy from the LLM."""

        self.client.force_login(self.staff_user)
        fake_response = SimpleNamespace(output=[{"content": [{"type": "output_text", "text": "Improved prompt"}]}])
        mock_client.responses.create.return_value = fake_response

        response = self.client.post(
            reverse("assets:enhance-prompt"),
            data=json.dumps({"text": "Original prompt"}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("enhanced_text"), "Improved prompt")
        mock_client.responses.create.assert_called_once()

    @patch("appertivo.assets.views.async_task")
    def test_generate_preview_flow(self, mock_async: Mock) -> None:
        """Posting generate returns a job identifier for polling."""

        self.client.force_login(self.staff_user)
        response = self.client.post(
            reverse("assets:dashboard"),
            {
                "action": "generate-asset",
                "generate-model": str(self.model.pk),
                "generate-prompt_text": "Create something nice",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertIn("job_id", payload)
        self.assertIn("status_url", payload)
        job = AssetPreviewJob.objects.get(pk=payload["job_id"])
        self.assertEqual(job.prompt, "Create something nice")
        self.assertEqual(job.status, AssetPreviewJob.Status.PENDING)
        mock_async.assert_called_once_with(
            "appertivo.assets.tasks.run_preview_job",
            job.pk,
        )

    @patch("appertivo.assets.tasks.run_preview_job")
    @patch("appertivo.assets.views.async_task")
    def test_generate_preview_filters_queue_kwargs(self, mock_async: Mock, mock_run: Mock) -> None:
        """Queue-specific kwargs are not passed to the job callable."""

        captured_ids: list[int] = []

        def fake_run(job_id: int) -> None:
            captured_ids.append(job_id)

        mock_run.side_effect = fake_run

        def fake_async(func_path: str, *args, timeout=None, **kwargs):
            call_kwargs = dict(kwargs)
            if timeout is not None:
                call_kwargs["timeout"] = timeout
            return queue_async_task(func_path, *args, **call_kwargs)

        mock_async.side_effect = fake_async

        self.client.force_login(self.staff_user)
        response = self.client.post(
            reverse("assets:dashboard"),
            {
                "action": "generate-asset",
                "generate-model": str(self.model.pk),
                "generate-prompt_text": "Queue options",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(captured_ids, [payload["job_id"]])
        self.assertEqual(mock_run.call_count, 1)

    @patch("appertivo.assets.views.async_task")
    def test_generate_preview_appends_additional_text(self, mock_async: Mock) -> None:
        """Freeform text augments the template instead of replacing it."""

        template = PromptTemplate.objects.create(title="Base", text="Base description")
        self.client.force_login(self.staff_user)
        response = self.client.post(
            reverse("assets:dashboard"),
            {
                "action": "generate-asset",
                "generate-model": str(self.model.pk),
                "generate-prompt_template": str(template.pk),
                "generate-prompt_text": "Extra spice",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 202)
        job = AssetPreviewJob.objects.get(pk=response.json()["job_id"])
        self.assertIn("Base description", job.prompt)
        self.assertIn("Extra spice", job.prompt)
        self.assertIn("\n\n", job.prompt)
        mock_async.assert_called_once()

    @patch("appertivo.assets.views.async_task")
    def test_generate_preview_requires_prompt(self, mock_async: Mock) -> None:
        """Missing prompt data surfaces a validation error response."""

        self.client.force_login(self.staff_user)
        response = self.client.post(
            reverse("assets:dashboard"),
            {
                "action": "generate-asset",
                "generate-model": str(self.model.pk),
                "generate-prompt_text": "",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("errors", payload)
        self.assertIn("prompt_text", payload["errors"])
        self.assertFalse(AssetPreviewJob.objects.exists())
        mock_async.assert_not_called()

    @patch("appertivo.assets.views.requests.get")
    def test_save_preview_persists_file(self, mock_get: Mock) -> None:
        """Saving the preview downloads the image and stores it on disk."""

        mock_response = SimpleNamespace(
            content=b"file-bytes",
            headers={"content-type": "image/png"},
        )
        mock_response.raise_for_status = lambda: None
        mock_get.return_value = mock_response

        self.client.force_login(self.staff_user)
        response = self.client.post(
            reverse("assets:dashboard"),
            {
                "action": "save-asset",
                "save-model_id": str(self.model.pk),
                "save-prompt_text": "A saved asset",
                "save-preview_url": "https://example.com/image.png",
            },
        )
        self.assertRedirects(response, reverse("assets:gallery"))
        asset = GeneratedAsset.objects.get()
        self.assertTrue(asset.image.name)
        self.assertEqual(asset.prompt, "A saved asset")
        self.assertEqual(asset.preview_url, "https://example.com/image.png")
        mock_get.assert_called_once_with("https://example.com/image.png", timeout=20)
        asset.image.delete(save=False)

    @patch("appertivo.assets.views.requests.get")
    def test_save_preview_uses_storage_path(self, mock_get: Mock) -> None:
        """Saving a preview produced from stored bytes avoids a network hop."""

        self.client.force_login(self.staff_user)
        stored_path = default_storage.save("previews/test-bytes.png", ContentFile(b"bytes"))
        self.assertFalse(Path(stored_path).suffix)
        response = self.client.post(
            reverse("assets:dashboard"),
            {
                "action": "save-asset",
                "save-model_id": str(self.model.pk),
                "save-prompt_text": "Stored asset",
                "save-preview_url": default_storage.url(stored_path),
                "save-storage_path": stored_path,
            },
        )
        self.assertRedirects(response, reverse("assets:gallery"))
        asset = GeneratedAsset.objects.get()
        self.assertTrue(asset.image.name)
        self.assertEqual(asset.prompt, "Stored asset")
        mock_get.assert_not_called()
        self.assertFalse(default_storage.exists(stored_path))
        asset.image.delete(save=False)

    @patch("appertivo.assets.tasks.replicate_client")
    def test_run_preview_job_records_url(self, mock_replicate: Mock) -> None:
        """A successful job stores the preview URL and marks the job as complete."""

        mock_replicate.run.return_value = ["https://example.com/image.png"]
        job = AssetPreviewJob.objects.create(model=self.model, prompt="Preview me")
        tasks.run_preview_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, AssetPreviewJob.Status.SUCCESS)
        self.assertEqual(job.preview_url, "https://example.com/image.png")
        self.assertEqual(job.storage_path, "")

    @patch("appertivo.assets.tasks.replicate_client")
    def test_run_preview_job_saves_bytes(self, mock_replicate: Mock) -> None:
        """Binary payloads from Replicate are saved to storage."""

        mock_replicate.run.return_value = [b"image-bytes"]
        job = AssetPreviewJob.objects.create(model=self.model, prompt="Bytes please")
        tasks.run_preview_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, AssetPreviewJob.Status.SUCCESS)
        self.assertTrue(job.preview_url)
        self.assertTrue(job.storage_path)
        self.assertFalse(Path(job.storage_path).suffix)
        self.assertTrue(default_storage.exists(job.storage_path))
        default_storage.delete(job.storage_path)

    @patch("appertivo.assets.tasks.replicate_client")
    def test_run_preview_job_handles_file_output(self, mock_replicate: Mock) -> None:
        """File-like outputs from Replicate are read and stored."""

        class DummyFile:
            def __init__(self, data: bytes) -> None:
                self._data = data

            def read(self) -> bytes:
                return self._data

        mock_replicate.run.return_value = DummyFile(b"file-bytes")
        job = AssetPreviewJob.objects.create(model=self.model, prompt="File please")
        tasks.run_preview_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, AssetPreviewJob.Status.SUCCESS)
        self.assertTrue(job.preview_url)
        self.assertTrue(job.storage_path)
        self.assertTrue(default_storage.exists(job.storage_path))
        default_storage.delete(job.storage_path)

    @patch("appertivo.assets.tasks.replicate_client")
    def test_run_preview_job_handles_errors(self, mock_replicate: Mock) -> None:
        """Unexpected errors mark the job as failed with a message."""

        mock_replicate.run.side_effect = RuntimeError("boom")
        job = AssetPreviewJob.objects.create(model=self.model, prompt="This will fail")
        tasks.run_preview_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, AssetPreviewJob.Status.FAILED)
        self.assertTrue(job.error_message)

    def test_gallery_view_lists_assets(self) -> None:
        """The gallery page renders saved items."""

        GeneratedAsset.objects.create(model=self.model, prompt="Test", preview_url="https://example.com/preview")
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("assets:gallery"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Saved assets")
        self.assertTemplateUsed(response, "assets/gallery.html")

    def test_preview_status_requires_staff(self) -> None:
        """The polling endpoint is staff-only."""

        job = AssetPreviewJob.objects.create(model=self.model, prompt="Check access")
        response = self.client.get(reverse("assets:preview-status", args=[job.pk]))
        self.assertEqual(response.status_code, 302)

    def test_preview_status_returns_payload(self) -> None:
        """Staff can retrieve job status and preview details as JSON."""

        job = AssetPreviewJob.objects.create(
            model=self.model,
            prompt="Check payload",
            status=AssetPreviewJob.Status.SUCCESS,
            preview_url="https://example.com/asset.png",
            storage_path="previews/test.png",
        )
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("assets:preview-status", args=[job.pk]))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], AssetPreviewJob.Status.SUCCESS)
        self.assertEqual(payload["preview_url"], "https://example.com/asset.png")
        self.assertEqual(payload["storage_path"], "previews/test.png")

    def test_verify_folder_pin_unlocks_session(self) -> None:
        """Submitting the correct PIN unlocks the folder for the session."""

        folder = AssetFolder.objects.create(name="Private", pin="4321", is_locked=True)
        self.client.force_login(self.staff_user)
        url = reverse("assets:verify-folder", args=[folder.pk])

        bad_response = self.client.post(
            url,
            data='{"pin": "0000"}',
            content_type="application/json",
        )
        self.assertEqual(bad_response.status_code, 400)
        self.assertNotIn("asset_unlocked_folders", self.client.session)

        good_response = self.client.post(
            url,
            data='{"pin": "4321"}',
            content_type="application/json",
        )
        self.assertEqual(good_response.status_code, 200)
        self.assertIn(folder.pk, self.client.session.get("asset_unlocked_folders", []))

        gallery = self.client.get(reverse("assets:gallery"))
        self.assertEqual(gallery.status_code, 200)
        self.assertIn(folder.pk, gallery.context["unlocked_folder_ids"])

    def test_recent_assets_hide_locked_folders(self) -> None:
        """Locked folders do not surface in the recent list."""

        self.client.force_login(self.staff_user)
        unlocked = AssetFolder.objects.create(name="Open", pin="5555", is_locked=False)
        locked = AssetFolder.objects.create(name="Private", pin="6666", is_locked=True)
        GeneratedAsset.objects.create(model=self.model, prompt="Visible", folder=unlocked)
        GeneratedAsset.objects.create(model=self.model, prompt="Hidden", folder=locked)

        response = self.client.get(reverse("assets:dashboard"))

        self.assertEqual(response.status_code, 200)
        recent = list(response.context["recent_assets"])
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].prompt, "Visible")

