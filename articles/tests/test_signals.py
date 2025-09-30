from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from articles.models import Article


class ArticleSignalTests(TestCase):
    def test_publish_generates_og_image_once(self):
        with patch("articles.signals.generate_article_og_image") as mock_generate:
            Article.objects.create(
                title="Draft Article",
                slug="draft-article",
                body_markdown="Body",
                summary="Summary",
            )
            mock_generate.assert_not_called()

        article = Article.objects.get(slug="draft-article")
        with patch(
            "articles.signals.generate_article_og_image",
            return_value="https://example.com/generated-og.jpg",
        ) as mock_generate:
            article.status = "published"
            article.save()
            mock_generate.assert_called_once()

        article.refresh_from_db()
        self.assertEqual(article.og_image_url, "https://example.com/generated-og.jpg")
