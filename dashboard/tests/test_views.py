"""Tests for the internal dashboard view."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app import models as app_models


class DashboardViewTests(TestCase):
    """Ensure the health dashboard renders with expected context."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.staff = user_model.objects.create_user(
            username="staff",
            email="staff@example.com",
            password="testpass123",
            is_staff=True,
        )
        self.account = app_models.Account.objects.create(name="Tasty Group")
        self.restaurant = app_models.Restaurant.objects.create(
            account=self.account,
            name="Cafe Napoli",
            location_text="SF",
        )
        self.plan = app_models.Plan.objects.create(
            code="pro",
            name="Pro",
            limits={},
            features={},
        )
        now = timezone.now()
        app_models.Subscription.objects.create(
            account=self.account,
            plan=self.plan,
            provider=app_models.Subscription.Provider.STRIPE,
            provider_customer_id="cust_1",
            provider_sub_id="sub_1",
            status=app_models.Subscription.Status.TRIALING,
            current_period_start=now - timedelta(days=2),
            current_period_end=now + timedelta(days=12),
        )
        app_models.Subscription.objects.create(
            account=self.account,
            plan=self.plan,
            provider=app_models.Subscription.Provider.STRIPE,
            provider_customer_id="cust_2",
            provider_sub_id="sub_2",
            status=app_models.Subscription.Status.CANCELED,
            current_period_start=now - timedelta(days=1),
            current_period_end=now,
        )
        self.new_user = user_model.objects.create_user(
            username="chef",
            email="chef@example.com",
            password="testpass123",
        )
        self.new_user.date_joined = now - timedelta(days=1)
        self.new_user.save(update_fields=["date_joined"])
        app_models.Onboarding.objects.create(
            user=self.new_user,
            restaurant=self.restaurant,
            state=app_models.Onboarding.State.EMAIL_CONFIRMED,
        )
        app_models.Job.objects.create(
            account=self.account,
            restaurant=self.restaurant,
            user=self.new_user,
            kind=app_models.Job.Kind.OUTSCRAPER,
            ref_table="onboarding",
            ref_id=uuid.uuid4(),
            status=app_models.Job.Status.FAILED,
            progress_pct=0,
            error_message="Timeout",
        )
        app_models.OutscraperPayload.objects.create(
            restaurant=self.restaurant,
            status=app_models.OutscraperPayload.Status.FAILED,
            request_params={},
            error_message="Webhook timeout",
        )
        app_models.MenuCollection.objects.create(
            restaurant=self.restaurant,
            created_by_user=self.new_user,
            name="Spring Menu",
        )
        app_models.Notification.objects.create(
            user=self.new_user,
            type=app_models.Notification.Type.OTHER,
            channel=app_models.Notification.Channel.EMAIL,
            payload={},
            status=app_models.Notification.Status.SENT,
        )
        app_models.Notification.objects.create(
            user=self.new_user,
            type=app_models.Notification.Type.OTHER,
            channel=app_models.Notification.Channel.EMAIL,
            payload={},
            status=app_models.Notification.Status.READ,
        )
        app_models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.new_user,
            type=app_models.IdeationRun.RunType.CONCEPTS,
            model_name="gpt-4",
            temperature=0.5,
            classic_creative=50,
            context_snapshot={},
            status=app_models.IdeationRun.Status.FAILED,
            cost_cents=0,
        )

    def test_dashboard_requires_staff(self) -> None:
        """Non-staff users should receive a forbidden response."""

        user_model = get_user_model()
        non_staff = user_model.objects.create_user(
            username="viewer",
            email="viewer@example.com",
            password="testpass123",
        )
        self.client.force_login(non_staff)
        response = self.client.get(reverse("dashboard:overview"))
        self.assertEqual(response.status_code, 403)

    def test_dashboard_renders_for_staff(self) -> None:
        """Staff can view the dashboard and receive aggregated context."""

        self.client.force_login(self.staff)
        response = self.client.get(reverse("dashboard:overview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SaaS Health Dashboard")
        self.assertIn("onboarding", response.context)
        self.assertIn("subscriptions", response.context)
        self.assertIn("operations", response.context)
        self.assertGreaterEqual(len(response.context["quick_actions"]), 1)


class LogFeedViewTests(TestCase):
    """Validate the staff-only JSON log feed."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.staff = user_model.objects.create_user(
            username="logstaff",
            email="logstaff@example.com",
            password="testpass123",
            is_staff=True,
        )
        self.viewer = user_model.objects.create_user(
            username="logviewer",
            email="logviewer@example.com",
            password="testpass123",
        )
        self.log_path = Path(settings.APP_LOG_FILE)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")

    def _write_entries(self, total: int) -> None:
        with self.log_path.open("w", encoding="utf-8") as stream:
            for index in range(total):
                entry = {"message": f"entry-{index}", "index": index}
                stream.write(json.dumps(entry) + "\n")

    def test_logs_require_staff(self) -> None:
        """Non-staff users should receive a forbidden response for the log feed."""

        self.client.force_login(self.viewer)
        response = self.client.get(reverse("dashboard:logs"))
        self.assertEqual(response.status_code, 403)

    def test_logs_return_empty_when_missing(self) -> None:
        """The endpoint should fall back to an empty array when the file is absent."""

        if self.log_path.exists():
            self.log_path.unlink()
        self.client.force_login(self.staff)
        response = self.client.get(reverse("dashboard:logs"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_logs_limit_to_recent_entries(self) -> None:
        """Only the most recent 200 log entries should be returned."""

        self._write_entries(205)
        self.client.force_login(self.staff)
        response = self.client.get(reverse("dashboard:logs"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 200)
        self.assertEqual(payload[0]["index"], 5)
        self.assertEqual(payload[-1]["index"], 204)
