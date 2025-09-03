from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from unittest.mock import patch
import time

from app.models import PipelineSession


class PipelineAdminTests(TestCase):
    """Tests for the pipeline session admin views."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser("admin", "admin@example.com", "pw")
        self.client.force_login(self.user)
        self.session = PipelineSession.objects.create(user=self.user, topic_hint="test")

    @patch("app.admin.run_next_step")
    def test_continue_view_runs_step_and_redirects(self, mock_run):
        mock_run.return_value = self.session
        url = reverse("admin:pipeline-continue", args=[self.session.pk])
        response = self.client.post(url, secure=True)
        self.assertEqual(response.status_code, 302)
        mock_run.assert_called_once_with(self.session)

    def test_research_step_async(self):
        self.session.current_step = "research"
        self.session.save()

        def delayed(session):
            session.research = "done"
            session.current_step = "draft"
            session.save()

        class DummyThread:
            def __init__(self, target, args=()):
                self.target = target
                self.args = args

            def start(self):
                self.target(*self.args)

        with patch("app.admin.run_next_step", side_effect=delayed), \
            patch("app.admin.Thread", DummyThread):
            url = reverse("admin:pipeline-continue", args=[self.session.pk])
            response = self.client.post(
                url, HTTP_X_REQUESTED_WITH="XMLHttpRequest", secure=True
            )
            self.assertEqual(response.status_code, 200)
            self.assertJSONEqual(response.content, {"status": "started"})

            status_url = reverse("admin:pipeline-status", args=[self.session.pk])
            data = self.client.get(status_url, secure=True).json()
            self.assertEqual(data["current_step"], "draft")
            self.assertEqual(
                PipelineSession.objects.get(pk=self.session.pk).research, "done"
            )

    def test_status_view_returns_current_step(self):
        url = reverse("admin:pipeline-status", args=[self.session.pk])
        response = self.client.get(url, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["current_step"], "ideas")
