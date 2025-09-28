from __future__ import annotations

from django.contrib.sitemaps import Sitemap

from .models import Article


class ArticlesSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        return Article.objects.filter(status="published").order_by("-published_at")

    def lastmod(self, obj: Article):
        return obj.published_at

    def location(self, obj: Article):
        return obj.get_absolute_url()
