from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import Article


class HomeFooterTests(TestCase):
    """Tests for the footer content on the home page."""

    def test_footer_contains_navigation_and_info(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, '<footer', html=False)
        for href in ['href="/"', 'href="/register/"', 'href="/login/"',
                     'href="/dashboard/"', 'href="/about/"',
                     'href="/contact/"']:
            with self.subTest(href=href):
                self.assertContains(response, href, html=False)
        self.assertContains(response, 'Future Articles')
        self.assertContains(response, 'Appertivo Inc.')
        for social in ['https://facebook.com', 'https://x.com', 'https://instagram.com']:
            with self.subTest(social=social):
                self.assertContains(response, social)

    def test_future_articles_list_shows_latest_five_articles(self):
        """Future Articles section lists links to the five latest articles."""
        for i in range(6):
            Article.objects.create(
                title=f"Article {i}",
                description="desc",
                content="content",
                published_at=timezone.now() + timezone.timedelta(days=i),
            )

        response = self.client.get(reverse('home'))
        latest_articles = Article.objects.order_by('-published_at')[:5]
        for article in latest_articles:
            with self.subTest(article=article.title):
                expected_link = (
                    f'<a href="{article.get_absolute_url()}" title="{article.title}" '
                    f'class="hover:underline">{article.title}</a>'
                )
                self.assertContains(response, expected_link, html=True)

        oldest = Article.objects.order_by('published_at').first()
        self.assertNotContains(response, oldest.title)
