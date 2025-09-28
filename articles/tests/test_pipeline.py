from __future__ import annotations

from django.test import TestCase
from articles.models import Article, ArticleRun, RunStep
from articles.pipeline import finalize_run


class ArticlePipelineTests(TestCase):
    def setUp(self):
        self.run = ArticleRun.objects.create()

    def _create_step(self, name: str, payload):
        return RunStep.objects.create(
            run=self.run,
            name=name,
            status="ok",
            output_payload=payload,
        )

    def test_finalize_run_creates_article_from_steps(self):
        self._create_step(
            "scoring",
            {
                "winner": {
                    "title": "Reduce Labor Costs Without Cutting Service",
                    "summary": "A quick summary",
                }
            },
        )
        self._create_step(
            "outline",
            {"outline": [{"heading": "Intro"}], "sources": [{"url": "https://example.com", "title": "Example"}]},
        )
        self._create_step(
            "draft",
            {
                "sections": [
                    {"h2": "Intro", "paragraphs": ["Paragraph one."]},
                ]
            },
        )
        self._create_step("polish", {"sections": []})
        self._create_step(
            "seo",
            {"seo_title": "Restaurant Labor Playbook", "seo_description": "Helpful tips", "slug": "labor-playbook"},
        )

        article = finalize_run(self.run)

        self.assertEqual(article.title, "Restaurant Labor Playbook")
        self.assertEqual(article.status, "draft")
        self.assertIn("Intro", article.body_markdown)
        self.assertEqual(article.slug, "labor-playbook")
        self.assertEqual(self.run.status, "completed")

    def test_article_save_resets_published_timestamp_for_drafts(self):
        article = Article.objects.create(
            title="Test Title",
            body_markdown="Body",
            summary="Summary",
            status="draft",
        )
        article.status = "published"
        article.save()
        self.assertIsNotNone(article.published_at)

        article.status = "draft"
        article.save()
        self.assertIsNone(article.published_at)
        self.assertTrue(article.slug.startswith("test-title"))
