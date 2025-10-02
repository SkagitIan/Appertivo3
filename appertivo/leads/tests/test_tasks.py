"""Tests for Outscraper integration tasks in the leads app."""
from __future__ import annotations

import json
import os
from unittest.mock import Mock, patch

import django
from django.test import TestCase, override_settings
from django.urls import reverse

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "specials.settings")
django.setup()

from appertivo.leads.models import Lead, LeadRun
from appertivo.leads.tasks import dispatch_lead_pipeline, fetch_leads


class FetchLeadsTaskTests(TestCase):
    """Verify the fetch_leads task integrates with Outscraper correctly."""

    @override_settings(OUTSCRAPER_API_KEY="test-key")
    @patch.dict(os.environ, {"OUTSCRAPER_API_KEY": "test-key"}, clear=False)
    @patch("appertivo.leads.tasks._create_outscraper_client")
    def test_fetch_leads_uses_sdk_and_maps_fields(self, mock_client_factory: Mock) -> None:
        run = LeadRun.objects.create(expected_leads=5)

        mock_client = Mock()
        mock_client.google_maps_search.return_value = {
            "id": "job-123",
            "data": [
                {
                    "name": "Sunset Bistro",
                    "email_1": "owner@example.com",
                    "email_2": "hello@sunset.example",
                    "phone": "555-0100",
                    "phone_2": "+1 555-0111",
                    "city": "Austin, TX",
                    "full_address": "123 Main St, Austin, TX",
                    "latitude": "30.2672",
                    "longitude": "-97.7431",
                    "site": "https://sunset.example",
                    "place_id": "place-123",
                    "instagram": "https://instagram.com/sunset",
                    "order_links": ["https://order.example"],
                    "working_hours": {"Monday": "9AM-5PM"},
                }
            ],
        }
        mock_client_factory.return_value = mock_client

        lead_ids = fetch_leads(run_id=run.id, city="Austin, TX", limit=3)

        self.assertEqual(len(lead_ids), 1)
        mock_client.google_maps_search.assert_called_once()
        _, kwargs = mock_client.google_maps_search.call_args
        self.assertEqual(kwargs["limit"], 3)
        self.assertIn("domains_service", kwargs["enrichment"])

        lead = Lead.objects.get(pk=lead_ids[0])
        self.assertEqual(lead.email, "owner@example.com")
        self.assertEqual(lead.json_data["name"], "Sunset Bistro")
        self.assertEqual(lead.full_address, "123 Main St, Austin, TX")
        self.assertAlmostEqual(lead.latitude, 30.2672)
        self.assertIn("https://order.example", lead.order_links)
        self.assertIn("instagram", lead.social_links)
        self.assertEqual(sorted(lead.emails), ["hello@sunset.example", "owner@example.com"])

        run.refresh_from_db()
        self.assertEqual(run.status, LeadRun.Status.PREPARING)
        self.assertEqual(run.outscraper_job_id, "job-123")

    @override_settings(OUTSCRAPER_API_KEY="test-key")
    @patch.dict(os.environ, {"OUTSCRAPER_API_KEY": "test-key"}, clear=False)
    @patch("appertivo.leads.tasks.logger")
    @patch("appertivo.leads.tasks._create_outscraper_client")
    def test_fetch_leads_logs_pending_jobs(self, mock_client_factory: Mock, mock_logger: Mock) -> None:
        mock_client = Mock()
        mock_client.google_maps_search.return_value = {
            "id": "job-456",
            "status": "Pending",
            "description": "Results are expired, or the task is not yet finished",
        }
        mock_client_factory.return_value = mock_client

        lead_ids = fetch_leads(city="Seattle, WA", limit=2)

        self.assertEqual(lead_ids, [])
        mock_logger.info.assert_any_call("Outscraper job %s not ready: %s", "job-456", "pending")


class DispatchLeadPipelineTests(TestCase):
    """Validate dispatch_lead_pipeline coordination behaviours."""

    def test_dispatch_pipeline_keeps_fetching_run_without_leads(self) -> None:
        run = LeadRun.objects.create(status=LeadRun.Status.FETCHING, total_leads=0, processed_leads=0)

        dispatch_lead_pipeline(lead_ids=[], run_id=run.id, send_email=False)

        run.refresh_from_db()
        self.assertEqual(run.status, LeadRun.Status.FETCHING)
        self.assertEqual(run.processed_leads, 0)


class OutscraperWebhookViewTests(TestCase):
    """Ensure the Outscraper webhook endpoint updates leads."""

    @override_settings(OUTSCRAPER_API_KEY="test-key")
    @patch.dict(os.environ, {"OUTSCRAPER_API_KEY": "test-key"}, clear=False)
    def test_webhook_updates_existing_lead_payload(self) -> None:
        lead = Lead.objects.create(name="Old Name", email="owner@example.com")

        payload = {
            "data": [
                {
                    "name": "Updated Name",
                    "email": "owner@example.com",
                    "city": "Miami, FL",
                    "phone": "555-0100",
                }
            ]
        }

        response = self.client.post(
            reverse("outscraper_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["processed"], 1)

        lead.refresh_from_db()
        self.assertEqual(lead.name, "Updated Name")
        self.assertEqual(lead.json_data["city"], "Miami, FL")

    @override_settings(OUTSCRAPER_API_KEY="test-key")
    @patch.dict(os.environ, {"OUTSCRAPER_API_KEY": "test-key"}, clear=False)
    @patch("appertivo.leads.views.dispatch_lead_pipeline.delay")
    def test_webhook_links_results_to_run(self, mock_delay: Mock) -> None:
        run = LeadRun.objects.create(city="Santa Fe, NM", outscraper_job_id="job-999", status=LeadRun.Status.FETCHING)

        payload = {
            "id": "job-999",
            "data": [
                {
                    "name": "Coyote Cafe",
                    "email": "chef@coyotecafe.com",
                    "city": "Santa Fe, NM",
                }
            ],
        }

        response = self.client.post(
            reverse("outscraper_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        lead = Lead.objects.get(email="chef@coyotecafe.com")
        self.assertEqual(lead.run, run)

        run.refresh_from_db()
        self.assertEqual(run.total_leads, 1)
        mock_delay.assert_called_once()
        args, kwargs = mock_delay.call_args
        self.assertIn(lead.id, args[0])
        self.assertEqual(kwargs["run_id"], run.id)
