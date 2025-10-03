"""Tests for public Stripe billing entry points."""

from types import SimpleNamespace
from unittest.mock import patch

import django
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

django.setup()


class BillingViewsTests(TestCase):
    """Verify marketing billing endpoints integrate with Stripe correctly."""

    @override_settings(
        STRIPE_PRICE_ID="price_123",
        STRIPE_SECRET_KEY="sk_test_123",
        MARKETING_DOMAIN="https://example.com",
    )
    def test_pricing_redirects_to_checkout(self):
        """GET /pricing should create a checkout session and redirect."""

        with patch("views.billing.stripe.checkout.Session.create") as mock_create:
            mock_create.return_value = SimpleNamespace(
                url="https://checkout.stripe.com/test"
            )
            response = self.client.get(reverse("pricing"))

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response["Location"], "https://checkout.stripe.com/test")

        kwargs = mock_create.call_args.kwargs
        self.assertEqual(kwargs["mode"], "subscription")
        self.assertEqual(kwargs["line_items"], [{"price": "price_123", "quantity": 1}])

    @override_settings(
        STRIPE_PRICE_ID="price_123",
        STRIPE_SECRET_KEY="sk_test_123",
        MARKETING_DOMAIN="https://example.com",
    )
    def test_create_checkout_session_redirects_to_stripe(self):
        """POSTing to the checkout endpoint should create a session and redirect."""

        with patch("views.billing.stripe.checkout.Session.create") as mock_create:
            mock_create.return_value = SimpleNamespace(
                url="https://checkout.stripe.com/test"
            )
            response = self.client.post(reverse("billing-create-checkout-session"))

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response["Location"], "https://checkout.stripe.com/test")

        kwargs = mock_create.call_args.kwargs
        self.assertEqual(kwargs["mode"], "subscription")
        self.assertEqual(kwargs["line_items"], [{"price": "price_123", "quantity": 1}])
        self.assertEqual(kwargs["consent_collection"], {"terms_of_service": "required"})
        self.assertEqual(kwargs["customer_creation"], "always")
        self.assertEqual(kwargs["payment_method_collection"], "always")
        self.assertEqual(
            kwargs["success_url"],
            "https://example.com/setup?session_id={CHECKOUT_SESSION_ID}",
        )
        self.assertEqual(kwargs["cancel_url"], "https://example.com/pricing")

    @override_settings(
        STRIPE_SECRET_KEY="sk_test_123",
        MARKETING_DOMAIN="https://example.com",
    )
    def test_create_billing_portal_session_redirects(self):
        """The billing portal endpoint returns the Stripe portal URL."""

        with patch("views.billing.stripe.billing_portal.Session.create") as mock_create:
            mock_create.return_value = SimpleNamespace(
                url="https://billing.stripe.com/abc"
            )
            response = self.client.post(
                reverse("billing-portal"),
                {"customer_id": "cus_123"},
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response["Location"], "https://billing.stripe.com/abc")

        kwargs = mock_create.call_args.kwargs
        self.assertEqual(kwargs["customer"], "cus_123")
        self.assertEqual(kwargs["return_url"], "https://example.com/setup")


class SetupPageTests(TestCase):
    """Ensure the setup placeholder renders successfully."""

    def test_setup_page_renders_with_session_id(self):
        """The setup page should render even with only a session id."""

        response = self.client.get(reverse("setup"), {"session_id": "cs_test_123"})

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"cs_test_123", response.content)
