import json
import os
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "specials.settings")
import django

django.setup()

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import reverse

from app import models, signup_service


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
        self.assertEqual(restaurant.menu_urls, [])
        self.assertEqual(body["redirect_url"], reverse("check-email"))

        mock_outscraper.delay.assert_not_called()
        mock_scrape.delay.assert_not_called()

    @patch(
        "app.views.signup_service.start_signup", wraps=signup_service.start_signup
    )
    @patch("app.views.run_outscraper_search")
    @patch("app.views.scrape_menu")
    def test_form_signup_renders_check_email(
        self, mock_scrape, mock_outscraper, mock_start_signup
    ):
        """HTML signup should rely on the signup helper and render check email."""
        form_data = {
            "email": "owner@example.com",
            "password1": "pw",
            "password2": "pw",
            "restaurant_name": "Tasty Place",
            "location": "City, State",
        }

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("signup"), data=form_data)

        mock_start_signup.assert_called_once()
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "auth/check_email.html")
        self.assertNotIn("_auth_user_id", self.client.session)

        mock_outscraper.delay.assert_not_called()
        mock_scrape.delay.assert_not_called()

    @patch("app.views.send_activation_email", create=True)
    @patch("app.views.scrape_menu", create=True)
    @patch("app.views.run_outscraper_search", create=True)
    @patch("app.views.create_checkout_session")
    def test_form_signup_stores_place_details_in_session(
        self, mock_checkout, mock_outscraper, mock_scrape_menu, mock_send_activation
    ):
        mock_checkout.return_value = HttpResponse()
        place_payload = {
            "place_id": "place_123",
            "formatted_address": "123 Test St, City",
            "latitude": 37.123456,
            "longitude": -122.987654,
            "formatted_phone_number": "+1 555-555-1234",
            "website": "https://example.com",
        }
        form_data = {
            "email": "owner2@example.com",
            "password1": "pw",
            "password2": "pw",
            "restaurant_name": "Second Place",
            "location": "City, State",
            "place_details_json": json.dumps(place_payload),
        }

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("signup"), data=form_data)

        onboarding = models.Onboarding.objects.get(user__username="owner2@example.com")
        details = self.client.session.get("signup_place_details", {}).get(str(onboarding.uuid))
        self.assertIsNotNone(details)
        self.assertEqual(details["place_id"], "place_123")
        self.assertEqual(details["formatted_address"], "123 Test St, City")
        mock_checkout.assert_called_once()

    def test_activation_redirects_to_getting_started(self):
        result = signup_service.start_signup(
            email="owner@example.com",
            password="pw",
            restaurant_name="Tasty Place",
            location="City",
        )

        token = result.activation_token
        response = self.client.get(reverse("activate-email", args=[token]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("getting-started"))
        self.assertIn("_auth_user_id", self.client.session)

    def test_form_signup_with_existing_email_shows_error(self):
        """Duplicate email addresses should not trigger a server error."""
        User.objects.create_user("owner@example.com", password="pw")

        form_data = {
            "email": "owner@example.com",
            "password1": "pw",
            "password2": "pw",
            "restaurant_name": "Tasty Place",
            "location": "City, State",
        }

        response = self.client.post(reverse("signup"), data=form_data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        # Ensure no duplicate accounts were created.
        self.assertEqual(User.objects.filter(username="owner@example.com").count(), 1)


    def test_login_with_bad_credentials_returns_error(self):
        """Failed logins should re-render the form with an error message."""
        response = self.client.post(
            reverse("login"), {"email": "missing@example.com", "password": "bad"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Double-check your email and password")

