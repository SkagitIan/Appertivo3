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
from appertivo.leads.tasks import fetch_leads


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
