"""Tests for the internal assets workspace views."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import django
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse

django.setup()

from appertivo.assets import tasks
from appertivo.assets.models import AssetModel, AssetPreviewJob, GeneratedAsset


class AssetDashboardTests(TestCase):
    """Exercise staff-only workflows for the asset studio."""

    def setUp(self) -> None:
        media_root = Path(tempfile.mkdtemp(prefix="appertivo-test-media-"))
        self.addCleanup(lambda: shutil.rmtree(media_root, ignore_errors=True))
        override = override_settings(MEDIA_ROOT=media_root)
        override.enable()
        self.addCleanup(override.disable)

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

    def test_create_model_and_prompt(self) -> None:
        """Staff can register models and prompts from the dashboard."""

        self.client.force_login(self.staff_user)
        response = self.client.post(
            reverse("assets:dashboard"),
            {
                "action": "create-model",
                "model-description": "High fidelity food",
                "model-identifier": "owner/food:456",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AssetModel.objects.filter(identifier="owner/food:456").exists())

        response = self.client.post(
            reverse("assets:dashboard"),
            {
                "action": "create-prompt",
                "prompt-title": "Hero shot",
                "prompt-text": "Stunning hero image",
            },
        )
        self.assertEqual(response.status_code, 302)
        from appertivo.assets.models import PromptTemplate

        self.assertTrue(PromptTemplate.objects.filter(title="Hero shot").exists())

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
        mock_async.assert_called_once_with("appertivo.assets.tasks.run_preview_job", job.pk)

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
