import os
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from app import models
from app.billing import create_checkout_session
from app.models import Subscription, UserProfile


class BillingTests(TestCase):
    """Tests for the billing page and subscription flow."""

    def setUp(self):
        self.user = User.objects.create_user(username="test@example.com", password="pass")
        UserProfile.objects.create(user=self.user)

    def test_billing_page_displays_single_plan(self):
        self.client.login(username="test@example.com", password="pass")
        response = self.client.get(reverse("billing"))
        self.assertContains(response, "$199")
        self.assertContains(response, "14-day free trial")

    def test_cancel_subscription_sets_cancelled_status(self):
        profile = UserProfile.objects.get(user=self.user)
        subscription = Subscription.objects.create(
            user=self.user,
            stripe_subscription_id="sub_123",
            signup_date=timezone.now(),
            trial_end_date=timezone.now() + timezone.timedelta(days=14),
            subscription_status="active",
        )
        profile.subscription_tier = "pro"
        profile.save()
        self.client.login(username="test@example.com", password="pass")
        response = self.client.post(reverse("cancel_subscription"))
        self.assertRedirects(response, reverse("billing"))
        subscription.refresh_from_db()
        self.assertEqual(subscription.subscription_status, "cancelled")
        profile.refresh_from_db()
        self.assertEqual(profile.subscription_tier, "free")


class CheckoutMetadataTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @patch("app.billing.stripe.checkout.Session.create")
    def test_checkout_includes_place_metadata(self, mock_create):
        mock_create.return_value = SimpleNamespace(url="https://stripe.example")
        onboarding_id = uuid.uuid4()
        request = self.factory.post("/billing/checkout")
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        request.session["signup_place_details"] = {
            str(onboarding_id): {
                "place_id": "place_123",
                "formatted_address": "123 Test St, City",
                "latitude": 40.123456,
                "longitude": -70.654321,
                "formatted_phone_number": "+1 555-0100",
                "website": "https://example.com",
            }
        }

        response = create_checkout_session(request, onboarding_id)

        self.assertEqual(response.status_code, 303)
        metadata = mock_create.call_args.kwargs["metadata"]
        self.assertEqual(metadata["place_id"], "place_123")
        self.assertEqual(metadata["place_address"], "123 Test St, City")
        self.assertEqual(metadata["place_lat"], "40.123456")
        self.assertEqual(metadata["place_lng"], "-70.654321")
        self.assertEqual(metadata["place_phone"], "+1 555-0100")
        self.assertEqual(metadata["place_website"], "https://example.com")
        self.assertNotIn(str(onboarding_id), request.session.get("signup_place_details", {}))


class StripeWebhookPlaceDetailsTests(TestCase):
    def setUp(self):
        os.environ.setdefault("STRIPE_TEST_WEBHOOK", "whsec_test")

    @patch("app.billing.run_onboarding_pipeline.delay")
    @patch("app.billing.stripe.Webhook.construct_event")
    def test_webhook_updates_restaurant(self, mock_construct, mock_pipeline):
        user = User.objects.create_user(username="webhook@example.com", password="pw")
        account = models.Account.objects.create(name="Webhook Restaurant")
        models.Membership.objects.create(account=account, user=user, role=models.Membership.Role.OWNER)
        restaurant = models.Restaurant.objects.create(
            account=account,
            name="Webhook Restaurant",
            location_text="Initial City",
        )
        onboarding = models.Onboarding.objects.create(
            user=user,
            restaurant=restaurant,
            activation_token="token",
        )

        metadata = {
            "onboarding_id": str(onboarding.uuid),
            "place_id": "place_456",
            "place_address": "456 Updated Ave, City",
            "place_lat": "45.000001",
            "place_lng": "-93.000001",
            "place_phone": "+1 555-0199",
            "place_website": "https://updated.example.com",
        }
        mock_construct.return_value = {
            "type": "checkout.session.completed",
            "data": {"object": {"metadata": metadata}},
        }

        response = self.client.post(
            reverse("stripe-webhook"),
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 200)
        restaurant.refresh_from_db()
        self.assertEqual(restaurant.google_place_id, "place_456")
        self.assertEqual(restaurant.location_text, "456 Updated Ave, City")
        self.assertEqual(str(restaurant.latitude), "45.000001")
        self.assertEqual(str(restaurant.longitude), "-93.000001")
        self.assertEqual(restaurant.phone, "+1 555-0199")
        self.assertEqual(restaurant.website, "https://updated.example.com")
        mock_pipeline.assert_called_once_with(str(onboarding.uuid))
