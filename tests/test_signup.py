import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from app import models, onboarding


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
        self.assertEqual(body["redirect_url"], reverse("onboarding"))

        onboarding_record = models.Onboarding.objects.get(
            user__username="owner@example.com"
        )
        self.assertEqual(onboarding_record.restaurant, restaurant)
        self.assertEqual(onboarding_record.state, models.Onboarding.State.EMAIL_CONFIRMED)
        self.assertEqual(onboarding_record.progress, 10)

        mock_outscraper.delay.assert_not_called()
        mock_scrape.delay.assert_not_called()

    @patch(
        "app.views.onboarding.start_signup", wraps=onboarding.start_signup
    )
    @patch("app.views.run_outscraper_search")
    @patch("app.views.scrape_menu")
    def test_form_signup_creates_onboarding_and_redirects(
        self, mock_scrape, mock_outscraper, mock_start_signup
    ):
        """HTML signup should rely on the onboarding helper and redirect."""
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
        restaurant = models.Restaurant.objects.get()
        self.assertRedirects(response, reverse("onboarding"))
        self.assertIn("_auth_user_id", self.client.session)

        onboarding_record = models.Onboarding.objects.get(
            user__username="owner@example.com"
        )
        self.assertEqual(onboarding_record.restaurant, restaurant)
        self.assertEqual(onboarding_record.state, models.Onboarding.State.EMAIL_CONFIRMED)
        self.assertEqual(onboarding_record.progress, 10)

        mock_outscraper.delay.assert_not_called()
        mock_scrape.delay.assert_not_called()

    @patch(
        "app.views.onboarding.start_signup", wraps=onboarding.start_signup
    )
    def test_onboarding_consent_updates_state(self, mock_start_signup):
        """Submitting consent should persist flags and keep progress updated."""

        form_data = {
            "email": "owner@example.com",
            "password1": "pw",
            "password2": "pw",
            "restaurant_name": "Tasty Place",
            "location": "City, State",
        }

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("signup"), data=form_data)

        mock_start_signup.assert_called_once()
        onboarding_record = models.Onboarding.objects.get(
            user__username="owner@example.com"
        )
        self.assertFalse(onboarding_record.accepted_terms)

        response = self.client.post(
            reverse("onboarding"),
            data={
                "form": "consent",
                "accepted_terms": "on",
                "accepted_privacy": "on",
                "authorized_data_fetch": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        onboarding_record.refresh_from_db()
        self.assertTrue(onboarding_record.accepted_terms)
        self.assertTrue(onboarding_record.accepted_privacy)
        self.assertTrue(onboarding_record.authorized_data_fetch)
        self.assertEqual(onboarding_record.state, models.Onboarding.State.EMAIL_CONFIRMED)
        self.assertGreaterEqual(onboarding_record.progress, 10)

    def test_onboarding_status_fragment_renders(self):
        """Status endpoint should return progress markup for HTMX polling."""

        form_data = {
            "email": "owner@example.com",
            "password1": "pw",
            "password2": "pw",
            "restaurant_name": "Tasty Place",
            "location": "City, State",
        }

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("signup"), data=form_data)

        response = self.client.get(reverse("onboarding-status"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Current state", content)
        self.assertIn("Email confirmed", content)

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

    @patch(
        "app.views.onboarding.start_signup", wraps=onboarding.start_signup
    )
    def test_onboarding_redirects_to_dashboard_when_complete(self, mock_start_signup):
        """Completing onboarding should send the user to the dashboard."""

        form_data = {
            "email": "owner@example.com",
            "password1": "pw",
            "password2": "pw",
            "restaurant_name": "Tasty Place",
            "location": "City, State",
        }

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("signup"), data=form_data)

        mock_start_signup.assert_called_once()
        onboarding_record = models.Onboarding.objects.get(
            user__username="owner@example.com"
        )
        restaurant = onboarding_record.restaurant
        onboarding_record.mark(models.Onboarding.State.COMPLETE, progress=100)

        response = self.client.get(reverse("onboarding"))

        self.assertRedirects(
            response, reverse("dashboard", args=[restaurant.id])
        )

    def test_login_with_bad_credentials_returns_error(self):
        """Failed logins should re-render the form with an error message."""
        response = self.client.post(
            reverse("login"), {"email": "missing@example.com", "password": "bad"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Double-check your email and password")

