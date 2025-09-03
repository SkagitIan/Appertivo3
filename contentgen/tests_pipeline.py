"""Tests for the content pipeline view."""
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.urls import reverse


class PipelineViewTests(TestCase):
    """Ensure pipeline steps render expected data."""

    @patch("contentgen.pipeline.ContentPipeline.brainstorm_ideas", return_value=["Idea 1", "Idea 2"])
    def test_brainstorm_step_shows_ideas(self, mock_brainstorm):
        response = self.client.get(reverse("contentgen:pipeline"))
        self.assertContains(response, "Idea 1")
        self.assertContains(response, "Continue")

    @patch("contentgen.tasks.deep_research_task.delay")
    def test_research_step_offloads_to_celery(self, mock_delay):
        # Mock Celery AsyncResult
        mock_result = MagicMock()
        mock_result.ready.return_value = True
        mock_result.get.return_value = {"sources": ["s1"]}
        mock_delay.return_value = mock_result

        session = self.client.session
        session["pipeline"] = {"brief": "brief"}
        session.save()

        response = self.client.post(reverse("contentgen:pipeline"), {"step": "research"})
        mock_delay.assert_called_once_with("brief")
        self.assertContains(response, "s1")
