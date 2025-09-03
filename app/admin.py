from django.contrib import admin
from django.apps import apps

app = apps.get_app_config('app')  # change 'profiles' to your app name


# app/admin.py
from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import render, redirect
from django import forms
# add at top
import logging
logger = logging.getLogger(__name__)

from .models import Article
from app.content_pipeline import save_article  # uses the code we wrote earlier


class GenerateArticleForm(forms.Form):
    """Simple admin form to kick off the pipeline."""
    topic_hint = forms.CharField(
        label="Topic hint",
        help_text="e.g., 'GBP Posts vs Offers (2025 guide) — for restaurateurs'",
        widget=forms.TextInput(attrs={"size": 80}),
    )


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "slug", "published_at")
    search_fields = ("title", "slug", "topic_hint", "seo_title", "seo_description")
    list_filter = ("status", "published_at")
    ordering = ("-published_at",)

    # Use a custom changelist template to show an "object tools" button.
    change_list_template = "admin/article_change_list.html"

    def get_urls(self):
        """Add /generate/ route under the Article admin."""
        urls = super().get_urls()
        custom = [
            path(
                "generate/",
                self.admin_site.admin_view(self.generate_view),
                name="app_article_generate",
            ),
        ]
        # Prepend so it wins before the default catch-alls
        return custom + urls

    def generate_view(self, request):
        if not self.has_add_permission(request):
            self.message_user(request, "No add permission.", level=messages.ERROR)
            return redirect("admin:app_article_changelist")

        if request.method == "POST":
            logger.info("[ArticleAdmin.generate_view] POST received")
            form = GenerateArticleForm(request.POST)
            if form.is_valid():
                topic_hint = form.cleaned_data["topic_hint"]
                logger.info(f"[ArticleAdmin] Starting pipeline for: {topic_hint!r}")
                try:
                    article, result = save_article(topic_hint)
                    dollars = result.get("usd_cost")
                    cost_str = f"${dollars:.2f}" if dollars is not None else \
                            f"{result.get('tokens_input',0)} in / {result.get('tokens_output',0)} out"
                    self.message_user(request, f"Article generated. Cost: {cost_str}", level=messages.SUCCESS)
                    change_url = reverse("admin:app_article_change", args=[article.pk])
                    logger.info(f"[ArticleAdmin] Success. Redirecting to change page id={article.pk}")
                    return redirect(change_url)
                except Exception as e:
                    logger.exception("[ArticleAdmin] Generation failed")
                    self.message_user(request, f"Generation failed: {e}", level=messages.ERROR)
                    # fall through to re-render form
            else:
                logger.warning(f"[ArticleAdmin] Form invalid: {form.errors.as_json()}")
                self.message_user(request, "Form invalid. Provide a topic hint.", level=messages.ERROR)
        else:
            logger.info("[ArticleAdmin.generate_view] GET render")

        form = GenerateArticleForm()
        context = dict(self.admin_site.each_context(request), opts=self.model._meta, form=form, title="Generate article")
        return render(request, "admin/generate_article.html", context)


for model_name, model in app.models.items():
    try:
        admin.site.register(model)
    except admin.sites.AlreadyRegistered:
        pass