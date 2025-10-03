"""Tests for the onboarding provisioning pipeline."""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from app import models
from onboarding import tasks


@override_settings(STRIPE_WEBHOOK_SECRET="")
class OnboardingPipelineTests(TestCase):
    """Integration-style tests covering onboarding orchestration."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="owner@example.com",
            email="owner@example.com",
            password="password123",
        )
        self.account = models.Account.objects.create(name="Test Account")
        models.Membership.objects.create(account=self.account, user=self.user)
        self.restaurant = models.Restaurant.objects.create(
            account=self.account,
            name="Pasta Place",
            location_text="Downtown",
        )
        self.onboarding = models.Onboarding.objects.create(
            user=self.user,
            restaurant=self.restaurant,
        )
        self.client = Client()
        self.client.login(username="owner@example.com", password="password123")

    def _run_successful_pipeline(self, job: models.ProvisioningJob) -> list[int]:
        """Run the orchestrator with mocked external services and return progress updates."""

        progress_values: list[int] = []
        original_mark = models.Onboarding.mark

        def capture_mark(self, state, progress=None, **kwargs):
            if progress is not None:
                progress_values.append(progress)
            return original_mark(self, state, progress=progress, **kwargs)

        context_payload = {
            "place_id": "place_123",
            "site": "https://pasta.example.com",
            "menu_link": "https://pasta.example.com/menu",
        }
        review_payload = [{"rating": 5, "text": "Amazing"}]
        profile_payload = {"menu_links": ["https://pasta.example.com/menu"], "contact": {}}
        personas_payload = ["Persona A", "Persona B", "Persona C"]
        analysis_payload = {"sentiment": "positive"}

        with (
            patch.object(models.Onboarding, "mark", autospec=True, side_effect=capture_mark),
            patch("onboarding.services.outscraper.fetch_context", return_value=context_payload),
            patch("onboarding.services.outscraper.fetch_reviews", return_value=review_payload),
            patch("onboarding.services.web_profile.build_profile", return_value=profile_payload),
            patch("onboarding.services.menu.snapshot_and_normalize") as mock_menu,
            patch("onboarding.tasks._run_review_analysis", return_value=analysis_payload),
            patch("onboarding.tasks._run_persona_generation", return_value=personas_payload),
        ):
            mock_menu.side_effect = lambda onboarding: models.MenuVersion.objects.create(
                restaurant=onboarding.restaurant,
                source_url="https://pasta.example.com/menu",
                source_kind=models.MenuVersion.SourceKind.URL_SCRAPE,
                raw_markdown="Sample menu",
                status=models.MenuVersion.Status.SUCCEEDED,
            )
            tasks.provision_onboarding.run(job.id)

        return progress_values

    def test_stripe_webhook_queues_orchestrator_once(self) -> None:
        """Stripe webhook stores event, job, and enqueues orchestrator once."""

        event = {
            "id": "evt_test",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test",
                    "customer_email": self.user.email,
                }
            },
        }

        with patch("onboarding.views.provision_onboarding.delay") as mock_delay:
            response = self.client.post(
                reverse("stripe_webhook"),
                data=json.dumps(event),
                content_type="application/json",
            )

        assert response.status_code == 200
        assert models.StripeWebhookEvent.objects.count() == 1
        job = models.ProvisioningJob.objects.get(onboarding=self.onboarding)
        assert job.stripe_session_id == "cs_test"
        mock_delay.assert_called_once_with(job.id)

        with patch("onboarding.views.provision_onboarding.delay") as mock_delay:
            repeat = self.client.post(
                reverse("stripe_webhook"),
                data=json.dumps(event),
                content_type="application/json",
            )
        assert repeat.status_code == 200
        mock_delay.assert_not_called()
        assert models.StripeWebhookEvent.objects.count() == 1

    def test_orchestrator_runs_steps_and_marks_progress(self) -> None:
        job = models.ProvisioningJob.objects.create(onboarding=self.onboarding)
        progress_values = self._run_successful_pipeline(job)

        self.onboarding.refresh_from_db()
        job.refresh_from_db()
        assert job.status == models.ProvisioningJob.Status.SUCCEEDED
        assert self.onboarding.progress == 100
        assert progress_values == [20, 35, 55, 70, 82, 92, 100]
        assert job.current_step == "finalize"
        assert models.Notification.objects.filter(user=self.user).exists()

    def test_orchestrator_is_idempotent_for_existing_data(self) -> None:
        first_job = models.ProvisioningJob.objects.create(onboarding=self.onboarding)
        self._run_successful_pipeline(first_job)

        initial_menus = models.MenuVersion.objects.count()
        initial_ingredients = models.Ingredient.objects.count()

        second_job = models.ProvisioningJob.objects.create(onboarding=self.onboarding)
        with patch("onboarding.services.menu.snapshot_and_normalize") as mock_snapshot:
            tasks.provision_onboarding.run(second_job.id)
        mock_snapshot.assert_not_called()

        assert models.MenuVersion.objects.count() == initial_menus
        assert models.Ingredient.objects.count() == initial_ingredients
        second_job.refresh_from_db()
        assert second_job.status == models.ProvisioningJob.Status.SUCCEEDED

    def test_failure_marks_onboarding_failed(self) -> None:
        job = models.ProvisioningJob.objects.create(onboarding=self.onboarding)

        context_payload = {
            "place_id": "place_123",
            "site": "https://pasta.example.com",
            "menu_link": "https://pasta.example.com/menu",
        }

        with (
            patch("onboarding.services.outscraper.fetch_context", return_value=context_payload),
            patch("onboarding.services.outscraper.fetch_reviews", return_value=[]),
            patch("onboarding.services.web_profile.build_profile", return_value={}),
            patch("onboarding.services.menu.snapshot_and_normalize") as mock_menu,
            patch("onboarding.tasks._run_review_analysis", side_effect=RuntimeError("llm down")),
        ):
            mock_menu.side_effect = lambda onboarding: models.MenuVersion.objects.create(
                restaurant=onboarding.restaurant,
                source_url="https://pasta.example.com/menu",
                source_kind=models.MenuVersion.SourceKind.URL_SCRAPE,
                raw_markdown="Sample menu",
                status=models.MenuVersion.Status.SUCCEEDED,
            )
            try:
                tasks.provision_onboarding.run(job.id)
            except RuntimeError:
                pass
            else:
                raise AssertionError("Expected RuntimeError not raised")

        job.refresh_from_db()
        self.onboarding.refresh_from_db()
        self.restaurant.refresh_from_db()
        assert job.status == models.ProvisioningJob.Status.FAILED
        assert self.onboarding.state == models.Onboarding.State.FAILED
        assert self.restaurant.context_json is not None

    def test_status_endpoint_requires_owner(self) -> None:
        models.ProvisioningJob.objects.create(onboarding=self.onboarding, current_step="menu_snapshot")
        self.onboarding.mark(models.Onboarding.State.MENU_DONE, progress=70)

        response = self.client.get(reverse("onboarding_status", args=[self.onboarding.id]))
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["state"] == self.onboarding.state
        assert data["current_step"] == "menu_snapshot"

        get_user_model().objects.create_user(
            username="other@example.com", email="other@example.com", password="pass"
        )
        self.client.logout()
        self.client.login(username="other@example.com", password="pass")
        forbidden = self.client.get(reverse("onboarding_status", args=[self.onboarding.id]))
        assert forbidden.status_code == 404
