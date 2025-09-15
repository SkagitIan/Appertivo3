import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from app import models


@override_settings(SECURE_SSL_REDIRECT=False)
class SignupViewTests(TestCase):
    """Tests for the signup endpoints."""

    def _api_payload(self, **overrides):
        base = {
            "email": "owner@example.com",
            "password": "pw",
            "restaurant_name": "Tasty Place",
            "location": "City, State",
        }
        base.update(overrides)
        return base

    @patch("app.views.run_outscraper_search")
    @patch("app.views.scrape_menu")
    def test_api_signup_creates_records_and_returns_redirect(self, mock_scrape, mock_outscraper):
        """JSON signup should create core objects and return a redirect URL."""
        payload = self._api_payload()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("api-signup"),
                data=json.dumps(payload),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("redirect_url", body)
        self.assertTrue(User.objects.filter(username="owner@example.com").exists())

        account = models.Account.objects.get()
        self.assertTrue(
            models.Membership.objects.filter(
                account=account, user__username="owner@example.com"
            ).exists()
        )
        restaurant = models.Restaurant.objects.get()
        self.assertEqual(restaurant.name, "Tasty Place")
        self.assertEqual(restaurant.location_text, "City, State")
        self.assertIsNone(restaurant.primary_menu_url)
        self.assertEqual(body["redirect_url"], reverse("dashboard", args=[restaurant.id]))

        payload_obj = models.OutscraperPayload.objects.get()
        self.assertEqual(payload_obj.status, models.OutscraperPayload.Status.QUEUED)
        self.assertIn("Tasty Place", payload_obj.request_params["query"])

        mock_outscraper.delay.assert_called_once_with(str(payload_obj.id))
        mock_scrape.delay.assert_not_called()

    @patch("app.views.run_outscraper_search")
    @patch("app.views.scrape_menu")
    def test_form_signup_without_menu_triggers_outscraper(self, mock_scrape, mock_outscraper):
        """HTML signup without menu URL should queue Outscraper and redirect."""
        form_data = {
            "email": "owner@example.com",
            "password1": "pw",
            "password2": "pw",
            "restaurant_name": "Tasty Place",
            "location": "City, State",
        }

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("signup"), data=form_data)

        restaurant = models.Restaurant.objects.get()
        self.assertRedirects(response, reverse("dashboard", args=[restaurant.id]))
        self.assertIn("_auth_user_id", self.client.session)

        payload_obj = models.OutscraperPayload.objects.get()
        self.assertEqual(payload_obj.restaurant, restaurant)
        mock_outscraper.delay.assert_called_once_with(str(payload_obj.id))
        mock_scrape.delay.assert_not_called()

    @patch("app.views.run_outscraper_search")
    @patch("app.views.scrape_menu")
    def test_form_signup_with_menu_url_scrapes_immediately(self, mock_scrape, mock_outscraper):
        """Providing a menu URL should create a queued menu version and scrape it."""
        form_data = {
            "email": "owner@example.com",
            "password1": "pw",
            "password2": "pw",
            "restaurant_name": "Tasty Place",
            "location": "City, State",
            "menu_url": "http://example.com/menu",
        }

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("signup"), data=form_data)

        restaurant = models.Restaurant.objects.get()
        self.assertRedirects(response, reverse("dashboard", args=[restaurant.id]))
        self.assertEqual(models.OutscraperPayload.objects.count(), 0)

        menu_version = models.MenuVersion.objects.get()
        self.assertEqual(menu_version.source_url, "http://example.com/menu")
        self.assertEqual(menu_version.status, models.MenuVersion.Status.QUEUED)
        self.assertEqual(menu_version.source_kind, models.MenuVersion.SourceKind.URL_SCRAPE)
        mock_scrape.delay.assert_called_once_with(str(menu_version.id))
        mock_outscraper.delay.assert_not_called()
        restaurant.refresh_from_db()
        self.assertEqual(restaurant.primary_menu_url, "http://example.com/menu")
