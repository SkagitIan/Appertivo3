from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from app import models
from app.llm_logging import log_llm_call


class LlmLoggingTests(TestCase):
    """Ensure the low-level logging helper records usage."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="logger@example.com", email="logger@example.com", password="pass1234"
        )

    def test_log_llm_call_records_tokens_and_cost(self):
        usage = SimpleNamespace(input_tokens=1200, output_tokens=600, total_tokens=1800)
        response = SimpleNamespace(usage=usage, id="resp_123")

        log_llm_call(
            user=self.user,
            provider="openai",
            model_name="gpt-4.1-mini",
            call_type=models.LlmCallLog.CallType.TEXT,
            step="unit_test",
            function_name="test_log_llm_call_records_tokens_and_cost",
            response=response,
            metadata={"note": "unit"},
        )

        record = models.LlmCallLog.objects.get()
        self.assertEqual(record.user, self.user)
        self.assertEqual(record.provider, "openai")
        self.assertEqual(record.model_name, "gpt-4.1-mini")
        self.assertEqual(record.call_type, models.LlmCallLog.CallType.TEXT)
        self.assertEqual(record.input_tokens, 1200)
        self.assertEqual(record.output_tokens, 600)
        self.assertGreaterEqual(record.cost_cents, 0)
        self.assertIn("usage", record.metadata)


class LlmAdminSummaryTests(TestCase):
    """Verify the admin changelist reports aggregated cost data."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="admin@example.com", email="admin@example.com", password="pass1234"
        )
        models.LlmCallLog.objects.create(
            user=self.superuser,
            provider="openai",
            model_name="gpt-4.1-mini",
            call_type=models.LlmCallLog.CallType.TEXT,
            step="test",
            function_name="fn",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            cost_cents=12,
            metadata={},
        )
        models.LlmCallLog.objects.create(
            user=None,
            provider="gemini",
            model_name="gemini-2.5-flash-image-preview",
            call_type=models.LlmCallLog.CallType.IMAGE,
            step="test",
            function_name="fn",
            cost_cents=5,
            metadata={},
        )

    def test_admin_cost_summary_context(self):
        self.client.login(username="admin@example.com", password="pass1234")
        response = self.client.get(reverse("admin:app_llmcalllog_changelist"))
        self.assertEqual(response.status_code, 200)
        summary = response.context["cost_summary"]
        self.assertEqual(summary["total_calls"], 2)
        self.assertEqual(summary["total_input"], 100)
        self.assertGreater(summary["total_cost"], 0)
        self.assertContains(response, "LLM Cost Summary")
