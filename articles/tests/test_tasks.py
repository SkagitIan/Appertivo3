from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.test import TestCase

from articles.models import ArticleRun, PromptTemplate, RunStep
from articles.tasks import run_step


class DummyResponse:
    def __init__(self, payload):
        self.payload = payload
        self.output_text = payload
        self.usage = SimpleNamespace(total_tokens=5)

    def model_dump(self):
        return {"output_text": self.payload}


class DummyClient:
    def __init__(self, payload):
        self.payload = payload
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        return DummyResponse(self.payload)


class RunStepTaskTests(TestCase):
    def setUp(self):
        PromptTemplate.objects.update_or_create(
            name="ideas", defaults={"prompt_text": "Ideas prompt"}
        )
        PromptTemplate.objects.update_or_create(
            name="scoring", defaults={"prompt_text": "Scoring prompt"}
        )
        self.run = ArticleRun.objects.create()

    def test_run_step_advances_pipeline(self):
        step = RunStep.objects.create(
            run=self.run,
            name="ideas",
            input_payload={"topic": "staffing"},
        )
        client = DummyClient('{"ideas": [{"title": "Idea"}], "notes": "Note"}')

        with mock.patch("articles.tasks.schedule_step") as mock_schedule:
            run_step(step.id, client=client)
            mock_schedule.assert_called()

        step.refresh_from_db()
        self.assertEqual(step.status, "ok")
        next_step = self.run.steps.filter(name="scoring").first()
        self.assertIsNotNone(next_step)
        self.assertEqual(next_step.input_payload.get("ideas")[0]["title"], "Idea")
