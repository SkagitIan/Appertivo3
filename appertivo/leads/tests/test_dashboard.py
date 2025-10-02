"""Tests for the lead dashboard workflow."""
from __future__ import annotations

from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from appertivo.leads.models import EmailTemplate, Lead, LeadRun


class _StubSignature:
    """Tiny helper to mimic a Celery signature for chaining in tests."""

    def __init__(self) -> None:
        self.chained: list[object] = []
        self.delayed = False

    def __or__(self, other: object) -> "_StubSignature":
        self.chained.append(other)
        return self

    def delay(self) -> None:
        self.delayed = True


class LeadDashboardTests(TestCase):
    """Verify the lead dashboard and actions behave as expected."""

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

    def test_dashboard_lists_leads_and_metrics(self) -> None:
        run = LeadRun.objects.create(city="Austin", status=LeadRun.Status.READY)
        Lead.objects.create(name="Morning Sun", city="Austin", email="owner@example.com", run=run)
        self.client.force_login(self.user)

        response = self.client.get(reverse("lead-dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lead workspace")
        self.assertContains(response, "Morning Sun")
        self.assertContains(response, "Lead status")
        self.assertContains(response, "Run pipeline")

    @patch("appertivo.leads.views.build_lead_run_pipeline")
    def test_start_lead_run_triggers_pipeline(self, mock_pipeline: Mock) -> None:
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
    def test_start_lead_run_accepts_custom_city(self, mock_pipeline: Mock) -> None:
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

    @patch("appertivo.leads.views.send_personalized_email")
    @patch("appertivo.leads.views.generate_concepts_and_dishes")
    def test_process_actions_approve_schedules_workflow(self, mock_generate: Mock, mock_email: Mock) -> None:
        mock_generate.s.return_value = _StubSignature()
        mock_email.s.return_value = object()
        run = LeadRun.objects.create(status=LeadRun.Status.READY)
        lead = Lead.objects.create(name="Cafe Rio", city="Denver", email="owner@example.com", run=run)
        template = EmailTemplate.objects.create(
            name="Outreach",
            subject="Hello {{business_name}}",
            body_text="Visit {{landing_page_url}}",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("lead-actions"),
            {"lead_ids": [str(lead.id)], "action": "approve", "template_id": str(template.id)},
        )
        self.assertRedirects(response, reverse("lead-dashboard"))

        lead.refresh_from_db()
        run.refresh_from_db()
        self.assertTrue(lead.shortlisted)
        self.assertFalse(lead.email_bounced)
        mock_generate.s.assert_called_once_with(lead.id)
        mock_email.s.assert_called_once_with(template.id)
        stub = mock_generate.s.return_value
        self.assertTrue(stub.delayed)
        self.assertEqual(run.selected_leads, 1)

    def test_process_actions_mark_bounced(self) -> None:
        run = LeadRun.objects.create(status=LeadRun.Status.READY)
        lead = Lead.objects.create(name="Blue Oak", city="Denver", email="owner@example.com", run=run)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("lead-actions"),
            {"lead_ids": [str(lead.id)], "action": "mark_bounced"},
        )
        self.assertRedirects(response, reverse("lead-dashboard"))

        lead.refresh_from_db()
        self.assertTrue(lead.email_bounced)

        response = self.client.post(
            reverse("lead-actions"),
            {"lead_ids": [str(lead.id)], "action": "clear_bounce"},
        )
        self.assertRedirects(response, reverse("lead-dashboard"))
        lead.refresh_from_db()
        self.assertFalse(lead.email_bounced)

    def test_process_actions_delete_removes_lead(self) -> None:
        run = LeadRun.objects.create(status=LeadRun.Status.READY, selected_leads=1)
        lead = Lead.objects.create(name="Wild Plum", city="Austin", shortlisted=True, run=run)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("lead-actions"),
            {"lead_ids": [str(lead.id)], "action": "delete"},
        )

        self.assertRedirects(response, reverse("lead-dashboard"))
        self.assertFalse(Lead.objects.filter(pk=lead.id).exists())
        run.refresh_from_db()
        self.assertEqual(run.selected_leads, 0)

    def test_delete_run_removes_run_and_leads(self) -> None:
        run = LeadRun.objects.create(status=LeadRun.Status.READY)
        Lead.objects.create(name="Sunrise Cafe", city="Dallas", run=run)
        self.client.force_login(self.user)

        response = self.client.post(reverse("lead-run-delete", args=[run.id]))
        self.assertRedirects(response, reverse("lead-dashboard"))

        self.assertFalse(LeadRun.objects.filter(pk=run.id).exists())
        self.assertEqual(Lead.objects.count(), 0)
