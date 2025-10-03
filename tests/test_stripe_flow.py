import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "specials.settings")
import django

django.setup()

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from app import models, onboarding, views as app_views


@override_settings(
    STRIPE_PRICE_ID="price_test",
    STRIPE_SECRET_KEY="sk_test",
    STRIPE_TRIAL_DAYS=14,
    STRIPE_WEBHOOK_SECRET="whsec_test",
)
class StripeFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("stripe@example.com", password="pw")
        self.account = models.Account.objects.create(name="Stripe Co")
        models.Membership.objects.create(account=self.account, user=self.user)
        self.restaurant = models.Restaurant.objects.create(
            account=self.account, name="Stripe Resto", location_text="City"
        )
        self.client.login(username="stripe@example.com", password="pw")
        onboarding.ensure_onboarding_for_user(self.user)

    @patch("app.views.stripe.checkout.Session.create")
    def test_start_trial_checkout_metadata(self, mock_checkout):
        mock_checkout.return_value = SimpleNamespace(url="https://stripe.test/session")

        response = self.client.post(
            reverse("billing-upgrade"), {"next": reverse("onboarding")}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://stripe.test/session")
        _, kwargs = mock_checkout.call_args
        self.assertEqual(kwargs["metadata"]["account_id"], str(self.account.id))
        self.assertEqual(kwargs["subscription_data"]["trial_period_days"], 14)
        self.assertEqual(kwargs["customer_email"], self.user.email)

    @patch("app.views.stripe.checkout.Session.create")
    def test_upgrade_sets_stripe_api_key(self, mock_checkout):
        mock_checkout.return_value = SimpleNamespace(url="https://stripe.test/session")
        app_views.stripe.api_key = ""

        response = self.client.post(
            reverse("billing-upgrade"), {"next": reverse("onboarding")}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(app_views.stripe.api_key, "sk_test")

    @patch("app.onboarding.task_send_welcome_email.delay")
    @patch("app.onboarding.kickoff_after_payment")
    @patch("app.views.stripe.Subscription.retrieve")
    @patch("app.views.stripe.Webhook.construct_event")
    def test_webhook_checkout_creates_subscription(
        self, mock_construct, mock_retrieve, mock_kickoff, mock_email
    ):
        onboarding_record = models.Onboarding.objects.get(user=self.user)
        event = {
            "id": "evt_123",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "subscription": "sub_123",
                    "metadata": {
                        "account_id": str(self.account.id),
                        "onboarding_id": str(onboarding_record.id),
                    },
                    "customer": "cus_123",
                    "id": "cs_test",
                }
            },
        }
        mock_construct.return_value = event
        mock_retrieve.return_value = {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "trialing",
            "current_period_start": 100,
            "current_period_end": 200,
            "cancel_at_period_end": False,
            "metadata": {"account_id": str(self.account.id)},
        }

        response = self.client.post(
            reverse("stripe-webhook"),
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 200)
        subscription = models.Subscription.objects.get(provider_sub_id="sub_123")
        self.assertEqual(subscription.status, "trialing")
        self.assertEqual(subscription.provider_customer_id, "cus_123")
        self.account.refresh_from_db()
        self.assertEqual(self.account.stripe_customer_id, "cus_123")

        job = models.ProvisioningJob.objects.get(onboarding=onboarding_record)
        self.assertEqual(job.stripe_session_id, "cs_test")
        self.assertEqual(job.last_stripe_event_id, "evt_123")
        mock_kickoff.assert_called_once()
        kickoff_args = mock_kickoff.call_args.args
        self.assertEqual(kickoff_args[0], onboarding_record.id)
        self.assertEqual(kickoff_args[1], job.id)
        mock_email.assert_called_once_with(str(onboarding_record.id))

        status_resp = self.client.get(reverse("onboarding-status-api"))
        state = status_resp.json()["state"]
        self.assertIn(
        state,
        {
            models.Onboarding.State.CHECKOUT_PAID,
            models.Onboarding.State.COMPLETE,
        },
        )

    @patch("app.onboarding.task_send_welcome_email.delay")
    @patch("app.onboarding.kickoff_after_payment")
    @patch("app.views.stripe.Webhook.construct_event")
    def test_webhook_dedupe_skips_duplicate_events(
        self, mock_construct, mock_kickoff, mock_email
    ):
        onboarding_record = models.Onboarding.objects.get(user=self.user)
        event = {
            "id": "evt_dupe",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "metadata": {
                        "onboarding_id": str(onboarding_record.id),
                        "account_id": str(self.account.id),
                    },
                    "id": "cs_dupe",
                }
            },
        }
        mock_construct.return_value = event

        for _ in range(2):
            response = self.client.post(
                reverse("stripe-webhook"),
                data="{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="sig",
            )
            self.assertEqual(response.status_code, 200)

        self.assertEqual(models.ProvisioningJob.objects.count(), 1)
        self.assertEqual(models.StripeWebhookEvent.objects.count(), 1)
        mock_kickoff.assert_called_once()
        mock_email.assert_called_once()

    def test_onboarding_status_api_respects_session_id(self):
        onboarding_record = models.Onboarding.objects.get(user=self.user)
        onboarding_record.state = models.Onboarding.State.FAILED
        onboarding_record.last_error = "generic"
        onboarding_record.save(update_fields=["state", "last_error", "updated_at"])

        job_one = models.ProvisioningJob.objects.create(
            onboarding=onboarding_record,
            stripe_session_id="sess_a",
            status=models.ProvisioningJob.Status.FAILED,
            current_step="reviews",
            error="step failed",
        )
        job_two = models.ProvisioningJob.objects.create(
            onboarding=onboarding_record,
            stripe_session_id="sess_b",
            status=models.ProvisioningJob.Status.RUNNING,
            current_step="personas",
        )

        resp_specific = self.client.get(
            reverse("onboarding-status-api"), {"session_id": "sess_a"}
        )
        payload = resp_specific.json()
        self.assertEqual(payload["job_status"], models.ProvisioningJob.Status.FAILED)
        self.assertEqual(payload["last_error"], "step failed")
        self.assertTrue(payload["can_retry"])

        resp_latest = self.client.get(reverse("onboarding-status-api"))
        payload_latest = resp_latest.json()
        self.assertEqual(payload_latest["job_status"], job_two.status)
        self.assertEqual(payload_latest["job_step"], "personas")

    @patch("app.onboarding.chain")
    def test_retry_view_creates_new_job(self, mock_chain):
        onboarding_record = models.Onboarding.objects.get(user=self.user)
        onboarding_record.state = models.Onboarding.State.FAILED
        onboarding_record.save(update_fields=["state", "updated_at"])
        models.ProvisioningJob.objects.create(
            onboarding=onboarding_record,
            stripe_session_id="sess_retry",
            status=models.ProvisioningJob.Status.FAILED,
            current_step="reviews",
            error="timeout",
        )

        response = self.client.post(
            reverse("onboarding-retry"),
            {"session_id": "sess_retry"},
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(models.ProvisioningJob.objects.count(), 2)
        mock_chain.assert_called_once()
