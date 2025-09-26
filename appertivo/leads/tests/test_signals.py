"""Tests for the lead conversion signal."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from appertivo.leads.models import Lead
from app.models import Membership


class LeadConversionSignalTests(TestCase):
    """Verify that user creation converts matching leads."""

    def test_user_signup_converts_lead(self) -> None:
        lead = Lead.objects.create(name="Signal Lead", email="lead@example.com", city="Austin, TX")
        user_model = get_user_model()
        user_model.objects.create_user(username="lead-user", email="lead@example.com", password="testpass123")

        lead.refresh_from_db()
        self.assertTrue(lead.converted)
        self.assertIsNotNone(lead.restaurant)
        self.assertEqual(lead.restaurant.name, lead.name)
        self.assertEqual(lead.restaurant.location_text, lead.city)
        self.assertTrue(Membership.objects.filter(account=lead.restaurant.account, user__email=lead.email).exists())
