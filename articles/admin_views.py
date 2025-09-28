from __future__ import annotations

from typing import Callable, Iterable

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse


def extend_admin_urls(original_get_urls: Callable[[], Iterable]) -> Callable[[], Iterable]:
    """Attach the articles dashboard to the admin without disrupting defaults."""

    def get_urls():
        urls = list(original_get_urls())
        custom = [
            path(
                "articles/dashboard/",
                admin.site.admin_view(admin_dashboard),
                name="articles_admin_dashboard",
            )
        ]
        return custom + urls

    return get_urls


@staff_member_required
def admin_dashboard(request):
    """Simple tabbed dashboard linking to key admin changelists."""

    tabs = [
        {
            "label": "Prompts",
            "description": "Manage the structured prompts for each pipeline step.",
            "url": reverse("admin:articles_prompttemplate_changelist"),
        },
        {
            "label": "Runs",
            "description": "Monitor automated article runs and recover from failures.",
            "url": reverse("admin:articles_articlerun_changelist"),
        },
        {
            "label": "Drafts",
            "description": "Review and publish generated articles.",
            "url": reverse("admin:articles_article_changelist"),
        },
    ]
    context = {
        **admin.site.each_context(request),
        "title": "Articles Pipeline",
        "tabs": tabs,
    }
    return TemplateResponse(request, "admin/articles/dashboard.html", context)


@staff_member_required
def dashboard_redirect(request):  # pragma: no cover - convenience redirect
    return redirect("articles_admin_dashboard")
