from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

try:
    from app.models import Article  # type: ignore
except ImportError:  # pragma: no cover - optional feature
    Article = None


class HomeFooterTests(TestCase):
    """Tests for the footer content on the home page."""

    def test_footer_contains_navigation_and_info(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, '<footer', html=False)

        for href in [
            'href="/privacy/"',
            'href="/terms/"',
            'href="/contact/"',
            'href="#app-showcase"',
            'href="#ai"',
            'href="#social-proof"',
            'href="#pricing"',
        ]:
            with self.subTest(href=href):
                self.assertContains(response, href, html=False)

        self.assertContains(response, 'Future Articles')
        self.assertContains(response, 'Appertivo')

        for social in [
            'https://www.instagram.com/appertivo',
            'https://www.linkedin.com/company/appertivo',
            'https://www.youtube.com/@appertivo',
        ]:
            with self.subTest(social=social):
                self.assertContains(response, social)

    def test_future_articles_list_shows_latest_articles(self):
        """Future Articles section lists links to the latest published articles."""
        if Article is None:
            self.skipTest("Article model is not available")

        for i in range(6):
            Article.objects.create(
                title=f"Article {i}",
                description="desc",
                content="content",
                published_at=timezone.now() + timezone.timedelta(days=i),
            )

        response = self.client.get(reverse('home'))
        latest_articles = Article.objects.order_by('-published_at')[:4]
        for article in latest_articles:
            with self.subTest(article=article.title):
                self.assertContains(
                    response,
                    f'href="{article.get_absolute_url()}"',
                    html=False,
                )
                self.assertContains(response, article.title)

        oldest = Article.objects.order_by('published_at').first()
        self.assertNotContains(response, oldest.title)
