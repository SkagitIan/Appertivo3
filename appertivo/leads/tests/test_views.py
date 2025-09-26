"""Tests for lead landing views."""
from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from appertivo.leads.models import Concept, DishIdea, Lead


class LeadLandingViewTests(TestCase):
    """Ensure the lead landing page renders and tracking works."""

    def setUp(self) -> None:
        self.lead = Lead.objects.create(name="Test Restaurant", city="Test City", slug="test-restaurant")
        Concept.objects.create(lead=self.lead, name="Concept A", rank_order=1, enhanced=True)
        DishIdea.objects.create(lead=self.lead, title="Dish One", favorited=True)

    def test_landing_page_renders(self) -> None:
        url = reverse("lead-landing", args=[self.lead.slug])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Restaurant")

    def test_track_open_sets_flag(self) -> None:
        track_url = reverse("lead-track", args=[self.lead.slug])
        response = self.client.get(track_url)
        self.assertEqual(response.status_code, 302)
        self.lead.refresh_from_db()
        self.assertTrue(self.lead.opened)
