from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from articles.models import ArticleRun, RunStep


class AdminDashboardViewTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.staff_user = user_model.objects.create_user(
            username="staff", email="staff@example.com", password="pass", is_staff=True
        )

    @patch("articles.pipeline.async_task")
    def test_start_article_run_returns_initial_progress(self, mock_async_task):
        self.client.force_login(self.staff_user)

        response = self.client.post(reverse("admin:articles_admin_start_run"))

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn("run", data)
        run_data = data["run"]
        self.assertEqual(run_data["status"], "queued")
        self.assertEqual(run_data["steps"][0]["name"], "ideas")
        self.assertEqual(run_data["steps"][0]["status"], "queued")
        self.assertTrue(any(step["status"] == "pending" for step in run_data["steps"][1:]))

        run = ArticleRun.objects.get(pk=run_data["id"])
        self.assertEqual(run.steps.count(), 1)
        mock_async_task.assert_called_once()

    def test_run_status_includes_existing_steps(self):
        self.client.force_login(self.staff_user)
        run = ArticleRun.objects.create(created_by=self.staff_user, status="running", current_step="outline")
        RunStep.objects.create(run=run, name="ideas", status="ok")
        RunStep.objects.create(run=run, name="scoring", status="ok")
        RunStep.objects.create(run=run, name="outline", status="running")

        response = self.client.get(reverse("admin:articles_admin_run_status", args=[run.id]))

        self.assertEqual(response.status_code, 200)
        data = response.json()["run"]
        outline_step = next(step for step in data["steps"] if step["name"] == "outline")
        scoring_step = next(step for step in data["steps"] if step["name"] == "scoring")

        self.assertEqual(scoring_step["status"], "ok")
        self.assertEqual(outline_step["status"], "running")
        self.assertTrue(outline_step["is_current"])

    def test_run_status_handles_missing_run(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("admin:articles_admin_run_status", args=[9999]))

        self.assertEqual(response.status_code, 404)
