from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from app import models


@override_settings(SECURE_SSL_REDIRECT=False)
class RestaurantStatusViewTests(TestCase):
    """Ensure status and menu upload flows behave end-to-end."""

    def setUp(self):
        self.account = models.Account.objects.create(name="Account")
        self.restaurant = models.Restaurant.objects.create(
            account=self.account,
            name="My Place",
            location_text="Town",
        )

    def test_status_pending_message_displayed(self):
        response = self.client.get(
            reverse("restaurant_status", args=[self.restaurant.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "We’re building your AI menu assistant")

    def test_status_prompts_for_menu_when_none_found(self):
        models.OutscraperPayload.objects.create(
            restaurant=self.restaurant,
            status=models.OutscraperPayload.Status.SUCCEEDED,
            request_params={},
            response_json={"data": []},
        )

        response = self.client.get(
            reverse("restaurant_status", args=[self.restaurant.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Provide menu")

    def test_status_ready_when_menu_available(self):
        menu_version = models.MenuVersion.objects.create(
            restaurant=self.restaurant,
            source_kind=models.MenuVersion.SourceKind.PASTED_TEXT,
            raw_markdown="Menu",
            status=models.MenuVersion.Status.SUCCEEDED,
        )
        self.restaurant.active_menu_version = menu_version
        self.restaurant.save(update_fields=["active_menu_version"])

        response = self.client.get(
            reverse("restaurant_status", args=[self.restaurant.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "✅ Your restaurant is ready!")

    def test_show_menu_modal_renders_form(self):
        response = self.client.get(
            reverse("show_menu_modal", args=[self.restaurant.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Provide Your Menu")
        self.assertContains(response, "name=\"menu_url\"")

    @patch("app.views.parse_pdf_menu")
    @patch("app.views.scrape_menu")
    def test_upload_menu_with_url_queues_scrape(self, mock_scrape, mock_parse):
        url = reverse("upload_menu", args=[self.restaurant.id])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, {"menu_url": "http://example.com/menu"})

        self.assertEqual(response.status_code, 200)
        menu_version = models.MenuVersion.objects.get()
        self.assertEqual(menu_version.status, models.MenuVersion.Status.QUEUED)
        self.assertEqual(menu_version.source_url, "http://example.com/menu")
        mock_scrape.delay.assert_called_once_with(str(menu_version.id))
        self.assertContains(response, "We’re building your AI menu assistant")
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.primary_menu_url, "http://example.com/menu")

    def test_upload_menu_with_text_marks_ready(self):
        url = reverse("upload_menu", args=[self.restaurant.id])
        response = self.client.post(url, {"menu_text": "Menu body"})

        self.assertEqual(response.status_code, 200)
        menu_version = models.MenuVersion.objects.get()
        self.assertEqual(menu_version.status, models.MenuVersion.Status.SUCCEEDED)
        self.assertEqual(menu_version.raw_markdown, "Menu body")
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.active_menu_version, menu_version)
        self.assertContains(response, "✅ Your restaurant is ready!")
