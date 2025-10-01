"""Tests for the internal assets workspace views."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import django
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

django.setup()

from appertivo.assets.models import AssetModel, GeneratedAsset


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

    @patch("appertivo.assets.views.replicate_client")
    def test_generate_preview_flow(self, mock_replicate) -> None:
        """Posting generate should surface the preview URL in the response."""

        mock_replicate.run.return_value = ["https://example.com/image.png"]
        self.client.force_login(self.staff_user)
        response = self.client.post(
            reverse("assets:dashboard"),
            {
                "action": "generate-asset",
                "generate-model": str(self.model.pk),
                "generate-prompt_text": "Create something nice",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("preview_url", response.context)
        self.assertEqual(response.context["preview_url"], "https://example.com/image.png")
        mock_replicate.run.assert_called_once()

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

    def test_gallery_view_lists_assets(self) -> None:
        """The gallery page renders saved items."""

        GeneratedAsset.objects.create(model=self.model, prompt="Test", preview_url="https://example.com/preview")
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("assets:gallery"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Saved assets")
        self.assertTemplateUsed(response, "assets/gallery.html")
