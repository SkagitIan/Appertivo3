"""Tests for the lead dashboard and run management views."""
from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from appertivo.leads.models import Lead, LeadRun


class LeadDashboardTests(TestCase):
    """Verify the lead dashboard workflow."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="lead-manager",
            email="manager@example.com",
            password="secret123",
        )

    def test_dashboard_requires_login(self) -> None:
        response = self.client.get(reverse("lead-dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_dashboard_renders_run_summary(self) -> None:
        run = LeadRun.objects.create(city="Austin", status=LeadRun.Status.READY)
        Lead.objects.create(name="Morning Sun", city="Austin", run=run)
        self.client.force_login(self.user)

        response = self.client.get(reverse("lead-dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Run {run.pk}")
        self.assertContains(response, "Morning Sun")

    def test_pending_run_shows_in_progress_indicator(self) -> None:
        run = LeadRun.objects.create(city="Boston", status=LeadRun.Status.FETCHING)
        self.client.force_login(self.user)

        response = self.client.get(reverse("lead-dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Runs in progress")
        self.assertContains(response, f"Run {run.pk}")
        self.assertContains(response, "Waiting for Outscraper")
        self.assertNotContains(response, "Shortlist</th>")
        self.assertNotContains(response, "No lead runs yet")

    @patch("appertivo.leads.views.build_lead_run_pipeline")
    def test_start_lead_run_triggers_pipeline(self, mock_pipeline) -> None:
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("lead-run-start"),
            {"city_choice": "Seattle, WA", "limit": "8"},
        )
        self.assertRedirects(response, reverse("lead-dashboard"))

        run = LeadRun.objects.get()
        self.assertEqual(run.city, "Seattle, WA")
        self.assertEqual(run.expected_leads, 8)
        self.assertEqual(run.status, LeadRun.Status.FETCHING)
        mock_pipeline.assert_called_once_with(run.id, city="Seattle, WA", limit=8)

    @patch("appertivo.leads.views.build_lead_run_pipeline")
    def test_start_lead_run_accepts_custom_city(self, mock_pipeline) -> None:
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("lead-run-start"),
            {"city_choice": "__custom__", "custom_city": "Boise, ID"},
        )
        self.assertRedirects(response, reverse("lead-dashboard"))

        run = LeadRun.objects.get()
        self.assertEqual(run.city, "Boise, ID")
        self.assertEqual(run.expected_leads, 10)
        self.assertEqual(run.status, LeadRun.Status.FETCHING)
        mock_pipeline.assert_called_once_with(run.id, city="Boise, ID", limit=10)

    @patch("appertivo.leads.views.send_personalized_email.delay")
    def test_update_run_selection_shortlists_and_sends_email(self, mock_delay) -> None:
        run = LeadRun.objects.create(status=LeadRun.Status.READY)
        selected = Lead.objects.create(name="Cafe Rio", city="Denver", email="owner@example.com", run=run)
        other = Lead.objects.create(name="Blue Oak", city="Denver", run=run)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("lead-run-selection", args=[run.id]),
            {"selected_leads": [str(selected.id)], "send_email": str(selected.id)},
        )
        self.assertRedirects(response, reverse("lead-dashboard"))

        selected.refresh_from_db()
        other.refresh_from_db()
        run.refresh_from_db()
        self.assertTrue(selected.shortlisted)
        self.assertFalse(other.shortlisted)
        self.assertEqual(run.selected_leads, 1)
        mock_delay.assert_called_once_with(selected.id)

    def test_update_run_selection_can_mark_complete(self) -> None:
        run = LeadRun.objects.create(status=LeadRun.Status.READY)
        lead = Lead.objects.create(name="Harvest Kitchen", city="Portland", run=run)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("lead-run-selection", args=[run.id]),
            {"mark_complete": "1"},
        )
        self.assertRedirects(response, reverse("lead-dashboard"))

        run.refresh_from_db()
        lead.refresh_from_db()
        self.assertEqual(run.status, LeadRun.Status.COMPLETED)
        self.assertFalse(lead.shortlisted)
        self.assertEqual(run.selected_leads, 0)

    def test_delete_run_removes_run_and_leads(self) -> None:
        run = LeadRun.objects.create(status=LeadRun.Status.READY)
        Lead.objects.create(name="Sunrise Cafe", city="Dallas", run=run)
        self.client.force_login(self.user)

        response = self.client.post(reverse("lead-run-delete", args=[run.id]))
        self.assertRedirects(response, reverse("lead-dashboard"))

        self.assertFalse(LeadRun.objects.filter(pk=run.id).exists())
        self.assertEqual(Lead.objects.count(), 0)
