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
    @patch("appertivo.leads.tasks.requests.get")
    def test_fetch_leads_uses_async_job_and_webhook(self, mock_get: Mock) -> None:
        run = LeadRun.objects.create(expected_leads=5)

        first_response = Mock()
        first_response.raise_for_status = Mock()
        first_response.json.return_value = {"id": "job-123", "status": "Pending"}

        second_response = Mock()
        second_response.raise_for_status = Mock()
        second_response.json.return_value = {
            "Status": "Success",
            "Data": [
                {
                    "name": "Sunset Bistro",
                    "email": "owner@example.com",
                    "phone": "555-0100",
                    "city": "Austin, TX",
                }
            ],
        }

        mock_get.side_effect = [first_response, second_response]

        lead_ids = fetch_leads(run_id=run.id, city="Austin, TX", limit=3)

        self.assertEqual(len(lead_ids), 1)
        self.assertEqual(
            mock_get.call_args_list[0].args[0],
            "https://api.outscraper.cloud/google-maps-search",
        )
        params = mock_get.call_args_list[0].kwargs["params"]
        self.assertEqual(params["async"], "true")
        self.assertEqual(params["webhook"], "https://appertivo.com/leads/outscraper-webhook/")
        self.assertEqual(params["enrichment"], json.dumps(["domains_service"]))
        self.assertEqual(
            mock_get.call_args_list[1].args[0],
            "https://api.outscraper.cloud/requests/job-123",
        )

        lead = Lead.objects.get(pk=lead_ids[0])
        self.assertEqual(lead.email, "owner@example.com")
        self.assertEqual(lead.json_data["name"], "Sunset Bistro")

        run.refresh_from_db()
        self.assertEqual(run.status, LeadRun.Status.PREPARING)
        self.assertEqual(run.outscraper_job_id, "job-123")

    @override_settings(OUTSCRAPER_API_KEY="test-key")
    @patch.dict(os.environ, {"OUTSCRAPER_API_KEY": "test-key"}, clear=False)
    @patch("appertivo.leads.tasks.logger")
    @patch("appertivo.leads.tasks.requests.get")
    def test_fetch_leads_logs_pending_jobs(self, mock_get: Mock, mock_logger: Mock) -> None:
        pending_response = Mock()
        pending_response.raise_for_status = Mock()
        pending_response.json.return_value = {
            "id": "job-456",
            "status": "Pending",
            "description": "Results are expired, or the task is not yet finished",
        }

        mock_get.return_value = pending_response

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
