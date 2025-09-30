from __future__ import annotations

import json
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

from articles.models import Article, ArticleRun, RunStep


class StaffDashboardTests(TestCase):
    def setUp(self) -> None:
        User = get_user_model()
        self.staff_user = User.objects.create_user(
            username="editor",
            email="editor@example.com",
            password="pass123",
            is_staff=True,
        )
        self.client.force_login(self.staff_user)

    def _make_openai_response(self, payload: dict, *, input_tokens: int = 10000, output_tokens: int = 10000):
        usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
        return SimpleNamespace(
            model_dump=lambda: {"output": payload},
            output_text=json.dumps(payload),
            usage=usage,
        )

    def test_dashboard_requires_staff(self):
        self.client.logout()
        response = self.client.get(reverse("articles:staff_dashboard"))
        self.assertEqual(response.status_code, 302)

    @patch("articles.views.get_openai_client")
    def test_generate_concepts_creates_run_and_partial(self, mock_client_factory):
        ideas_payload = {
            "ideas": [
                {"title": "Idea One", "subtitle": "Subtitle", "angle": "Angle"},
                {"title": "Idea Two", "subtitle": "Subtitle", "angle": "Angle"},
            ],
            "notes": "Focus on independent operators.",
        }
        mock_client = SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **_: self._make_openai_response(ideas_payload)
            )
        )
        mock_client_factory.return_value = mock_client

        response = self.client.post(
            reverse("articles:staff_generate_concepts"),
            {
                "topic": "Inventory tactics",
                "context": "Recent interviews about food waste.",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Concepts ready for review")
        run = ArticleRun.objects.get()
        self.assertEqual(run.created_by, self.staff_user)
        self.assertEqual(run.current_step, "ideas")
        ideas_step = RunStep.objects.get(run=run, name="ideas")
        self.assertEqual(len(ideas_step.output_payload["ideas"]), 2)
        self.assertGreater(run.cost_cents, 0)
        self.assertEqual(ideas_step.input_payload.get("pdf_context"), "")

    @patch("articles.views.get_openai_client")
    def test_select_concept_creates_draft_step(self, mock_client_factory):
        run = ArticleRun.objects.create(created_by=self.staff_user, status="running", current_step="ideas")
        RunStep.objects.create(
            run=run,
            name="ideas",
            status="ok",
            input_payload={"topic": "Inventory", "context": "Inventory", "pdf_context": "Research"},
            output_payload={
                "ideas": [
                    {"title": "Idea One", "subtitle": "Subtitle"},
                    {"title": "Idea Two", "subtitle": "Subtitle"},
                ],
                "notes": "Notes",
            },
        )
        draft_payload = {
            "summary": "Outline summary",
            "citations": [{"title": "Source", "url": "https://example.com"}],
            "draft": {
                "title": "Idea One",
                "sections": [
                    {"heading": "Intro", "paragraphs": ["Paragraph text"]},
                ],
            },
        }
        mock_client = SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **_: self._make_openai_response(draft_payload)
            )
        )
        mock_client_factory.return_value = mock_client

        response = self.client.post(
            reverse("articles:staff_select_concept"),
            {"run_id": run.id, "idea_index": 0},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Research summary")
        run.refresh_from_db()
        draft_step = RunStep.objects.get(run=run, name="draft")
        self.assertEqual(draft_step.output_payload["summary"], "Outline summary")
        self.assertGreater(run.cost_cents, 0)

    @patch("articles.views.extract_pdf_text", return_value="PDF insights")
    @patch("articles.views.get_openai_client")
    def test_generate_concepts_uses_pdf_upload(self, mock_client_factory, mock_extract):
        ideas_payload = {
            "ideas": [
                {"title": "Idea One", "subtitle": "Subtitle", "angle": "Angle"},
            ],
        }
        mock_client = SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **_: self._make_openai_response(ideas_payload)
            )
        )
        mock_client_factory.return_value = mock_client

        pdf_file = SimpleUploadedFile("notes.pdf", b"%PDF-1.4 sample", content_type="application/pdf")

        response = self.client.post(
            reverse("articles:staff_generate_concepts"),
            {
                "topic": "Inventory tactics",
                "context": "Recent interviews about food waste.",
                "pdf_upload": pdf_file,
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        mock_extract.assert_called_once()
        run = ArticleRun.objects.get()
        ideas_step = RunStep.objects.get(run=run, name="ideas")
        self.assertEqual(ideas_step.input_payload.get("pdf_context"), "PDF insights")

    @patch("articles.views.get_openai_client")
    def test_finalize_article_creates_article(self, mock_client_factory):
        run = ArticleRun.objects.create(created_by=self.staff_user, status="running", current_step="draft")
        RunStep.objects.create(
            run=run,
            name="ideas",
            status="ok",
            input_payload={},
            output_payload={"ideas": [{"title": "Idea One", "subtitle": ""}]},
        )
        RunStep.objects.create(
            run=run,
            name="draft",
            status="ok",
            input_payload={"selected": {"title": "Idea One"}},
            output_payload={
                "summary": "Draft summary",
                "citations": [{"title": "Source", "url": "https://example.com"}],
                "draft_markdown": "## Intro\nBody",
                "idea_index": 0,
            },
        )
        final_payload = {
            "title": "Final Title",
            "summary": "Final summary",
            "body_markdown": "## Intro\nBody",
            "seo_title": "SEO Title",
            "seo_description": "SEO Description",
            "sources": [{"title": "Source", "url": "https://example.com"}],
        }
        mock_client = SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **_: self._make_openai_response(final_payload)
            )
        )
        mock_client_factory.return_value = mock_client

        response = self.client.post(
            reverse("articles:staff_finalize_article"),
            {
                "run_id": run.id,
                "draft_title": "Final Title",
                "draft_body": "## Intro\nBody",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Final article review")
        run.refresh_from_db()
        article = Article.objects.get(run=run)
        self.assertEqual(article.title, "Final Title")
        self.assertEqual(article.status, "draft")
        self.assertEqual(run.status, "completed")

    def test_publish_article_sets_published(self):
        run = ArticleRun.objects.create(created_by=self.staff_user, status="completed")
        article = Article.objects.create(
            title="Draft",
            slug="draft",
            body_markdown="Body",
            run=run,
        )

        response = self.client.post(
            reverse("articles:staff_publish_article"),
            {"run_id": run.id, "article_id": article.id},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        article.refresh_from_db()
        self.assertEqual(article.status, "published")

    def test_runs_fragment_renders(self):
        ArticleRun.objects.create(created_by=self.staff_user)
        response = self.client.get(reverse("articles:staff_runs_fragment"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Run #")
