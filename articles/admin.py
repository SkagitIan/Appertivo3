from __future__ import annotations

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from . import admin_views
from .models import Article, ArticleRun, PromptTemplate, RunStep


@admin.register(PromptTemplate)
class PromptTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "last_updated")
    search_fields = ("name", "prompt_text")


@admin.register(ArticleRun)
class ArticleRunAdmin(admin.ModelAdmin):
    list_display = ("id", "created_by", "status", "current_step", "created_at")
    readonly_fields = ("created_at", "error_message", "cost_cents")
    search_fields = ("id", "created_by__email")
    list_filter = ("status", "model_info")

    def changelist_view(self, request, extra_context=None):  # pragma: no cover - thin wrapper
        extra_context = extra_context or {}
        extra_context.setdefault(
            "articles_dashboard_url",
            reverse("articles_admin_dashboard"),
        )
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(RunStep)
class RunStepAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "name", "status", "started_at", "ended_at")
    readonly_fields = ("input_payload", "output_payload", "raw_response")
    search_fields = ("name", "run__id")
    list_filter = ("status", "name")


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "published_at")
    list_filter = ("status",)
    prepopulated_fields = {"slug": ("title",)}
    search_fields = ("title", "summary", "body_markdown")
    readonly_fields = ("run",)

    def changelist_view(self, request, extra_context=None):  # pragma: no cover - thin wrapper
        extra_context = extra_context or {}
        extra_context.setdefault(
            "articles_dashboard_url",
            reverse("articles_admin_dashboard"),
        )
        return super().changelist_view(request, extra_context=extra_context)


# Hook the custom dashboard into the default admin site navigation.
def _inject_dashboard_link(original_index):  # pragma: no cover - glue code
    def wrapped(request, extra_context=None):
        extra_context = extra_context or {}
        extra_context.setdefault(
            "articles_dashboard",
            format_html("<a href='{}'>{}</a>", reverse("articles_admin_dashboard"), _( "Articles")),
        )
        return original_index(request, extra_context=extra_context)

    return wrapped


admin.site.index = _inject_dashboard_link(admin.site.index)

# Ensure the dashboard view is registered once admin is ready.
admin.site.get_urls = admin_views.extend_admin_urls(admin.site.get_urls)
