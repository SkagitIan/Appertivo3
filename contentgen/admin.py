"""Admin registrations for content generation models."""
from django import forms
from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import render, redirect
import logging

from .models import Article, ArticleRevision, Idea, SeedDoc
from .pipeline import save_article

logger = logging.getLogger(__name__)


@admin.register(SeedDoc)
class SeedDocAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")


@admin.register(Idea)
class IdeaAdmin(admin.ModelAdmin):
    list_display = ("title", "score", "status", "created_at")
    list_filter = ("status",)


class GenerateArticleForm(forms.Form):
    """Simple admin form to kick off the pipeline."""
    topic_hint = forms.CharField(
        label="Topic hint",
        help_text="e.g., 'GBP Posts vs Offers (2025 guide) — for restaurateurs'",
        widget=forms.TextInput(attrs={"size": 80}),
    )


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "published_at")
    list_filter = ("status",)
    prepopulated_fields = {"slug": ("title",)}
    change_list_template = "admin/article_change_list.html"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "generate/",
                self.admin_site.admin_view(self.generate_view),
                name="contentgen_article_generate",
            ),
        ]
        return custom + urls

    def generate_view(self, request):
        if not self.has_add_permission(request):
            self.message_user(request, "No add permission.", level=messages.ERROR)
            return redirect("admin:contentgen_article_changelist")

        if request.method == "POST":
            form = GenerateArticleForm(request.POST)
            if form.is_valid():
                topic_hint = form.cleaned_data["topic_hint"]
                try:
                    article, result = save_article(topic_hint)
                    self.message_user(request, "Article generated.", level=messages.SUCCESS)
                    return redirect(reverse("admin:contentgen_article_change", args=[article.pk]))
                except Exception as e:
                    logger.exception("[ArticleAdmin] Generation failed")
                    self.message_user(request, f"Generation failed: {e}", level=messages.ERROR)
            else:
                self.message_user(request, "Form invalid. Provide a topic hint.", level=messages.ERROR)
        else:
            form = GenerateArticleForm()
        context = dict(self.admin_site.each_context(request), opts=self.model._meta, form=form, title="Generate article")
        return render(request, "admin/generate_article.html", context)


@admin.register(ArticleRevision)
class ArticleRevisionAdmin(admin.ModelAdmin):
    list_display = ("article", "step", "created_at")
    list_filter = ("step",)
