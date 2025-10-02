"""Tests for the internal asset studio workspace views."""

from __future__ import annotations

import os
import tempfile
import uuid

import django
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.functional import empty

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "specials.settings")
django.setup()

from appertivo.assets.models import AssetFolder, AssetModel, GeneratedAsset


class AssetWorkspaceTests(TestCase):
    """Ensure staff can manage folders, previews, and saved assets."""

    def setUp(self) -> None:
        super().setUp()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username=f"staff-{uuid.uuid4().hex[:8]}",
            email="staff@example.com",
            password="password123",
        )
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        self.client = Client()
        self.client.force_login(self.user)

    def _create_model(self) -> AssetModel:
        return AssetModel.objects.create(
            description="Test Model",
            identifier=f"owner/test-model:{uuid.uuid4().hex}",
        )

    def test_create_folder(self) -> None:
        response = self.client.post(
            reverse("assets:gallery"),
            {"action": "create-folder", "folder-name": "Menus"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(AssetFolder.objects.filter(name="Menus").exists())

    def test_assign_and_remove_folder(self) -> None:
        model = self._create_model()
        asset = GeneratedAsset.objects.create(model=model, prompt="Test prompt")
        folder = AssetFolder.objects.create(name="Decks")

        assign_response = self.client.post(
            reverse("assets:gallery"),
            {
                "action": "assign-folder",
                "asset_id": asset.pk,
                "folder_id": folder.pk,
            },
        )
        self.assertEqual(assign_response.status_code, 302)
        asset.refresh_from_db()
        self.assertEqual(asset.folder, folder)

        remove_response = self.client.post(
            reverse("assets:gallery"),
            {
                "action": "assign-folder",
                "asset_id": asset.pk,
                "folder_id": "",
            },
        )
        self.assertEqual(remove_response.status_code, 302)
        asset.refresh_from_db()
        self.assertIsNone(asset.folder)

    def test_delete_asset_removes_file(self) -> None:
        model = self._create_model()
        asset = GeneratedAsset.objects.create(model=model, prompt="To delete")

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=tmpdir):
            default_storage._wrapped = empty  # type: ignore[attr-defined]
            asset.image.save("delete-me.png", ContentFile(b"file-bytes"), save=True)
            stored_name = asset.image.name

            response = self.client.post(
                reverse("assets:gallery"),
                {"action": "delete-asset", "asset_id": asset.pk},
            )

            self.assertEqual(response.status_code, 302)
            self.assertFalse(GeneratedAsset.objects.filter(pk=asset.pk).exists())
            self.assertFalse(default_storage.exists(stored_name))

    def test_discard_preview_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=tmpdir):
            default_storage._wrapped = empty  # type: ignore[attr-defined]
            stored_path = default_storage.save("previews/example.png", ContentFile(b"preview"))
            self.assertTrue(default_storage.exists(stored_path))

            response = self.client.post(
                reverse("assets:discard-preview"),
                data={"storage_path": stored_path},
            )

            self.assertEqual(response.status_code, 200)
            self.assertFalse(default_storage.exists(stored_path))

            invalid_response = self.client.post(
                reverse("assets:discard-preview"),
                data={"storage_path": "../etc/passwd"},
            )

            self.assertEqual(invalid_response.status_code, 400)
