from __future__ import annotations

from django.http import Http404
from django.shortcuts import get_object_or_404, render

from .models import Article


def article_index(request):
    articles = (
        Article.objects.filter(status="published")
        .order_by("-published_at")
        .only("title", "summary", "slug", "published_at", "seo_description")
    )
    return render(
        request,
        "articles/index.html",
        {
            "articles": articles,
        },
    )


def article_detail(request, year: int, month: int, slug: str):
    article = get_object_or_404(Article, slug=slug, status="published")
    if not article.published_at:
        raise Http404("Article is not published")

    expected_year = article.published_at.year
    expected_month = int(article.published_at.strftime("%m"))
    if expected_year != int(year) or expected_month != int(month):
        raise Http404("Article date mismatch")

    return render(
        request,
        "articles/detail.html",
        {
            "article": article,
        },
    )
