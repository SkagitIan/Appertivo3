from types import SimpleNamespace
from unittest.mock import patch

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

    @patch("app.views.stripe.Subscription.retrieve")
    @patch("app.views.stripe.Webhook.construct_event")
    def test_webhook_checkout_creates_subscription(self, mock_construct, mock_retrieve):
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "subscription": "sub_123",
                    "metadata": {"account_id": str(self.account.id)},
                    "customer": "cus_123",
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

        status_resp = self.client.get(reverse("onboarding-status-api"))
        state = status_resp.json()["state"]
        self.assertIn(
            state,
            {
                models.Onboarding.State.CHECKOUT_PAID,
                models.Onboarding.State.COMPLETE,
            },
        )
