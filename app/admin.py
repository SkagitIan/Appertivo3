from django.contrib import admin
from django.apps import apps

app = apps.get_app_config('app')  # change 'profiles' to your app name


# app/admin.py
from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import render, redirect
from django import forms
from django.http import JsonResponse
from threading import Thread
import logging
logger = logging.getLogger(__name__)

from .models import Article
from django.utils.html import format_html

from .models import PipelineSession
from app.pipeline_runner import run_next_step

@admin.register(PipelineSession)
class PipelineSessionAdmin(admin.ModelAdmin):
    list_display = ("topic_hint", "current_step", "status", "created_at")
    readonly_fields = (
        "ideas", "picked", "brief", "research", "draft", "edited", "seo", "html"
    )

    change_form_template = "admin/pipeline_session_changeform.html"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:pk>/continue/",
                self.admin_site.admin_view(self.continue_view),
                name="pipeline-continue",
            ),
            path(
                "<path:pk>/status/",
                self.admin_site.admin_view(self.status_view),
                name="pipeline-status",
            ),
        ]
        return custom_urls + urls

    def continue_view(self, request, pk):
        """Run the next step, handling research asynchronously."""
        session = PipelineSession.objects.get(pk=pk)
        if (
            session.current_step == "research"
            and request.headers.get("x-requested-with") == "XMLHttpRequest"
        ):
            Thread(target=run_next_step, args=(session,)).start()
            return JsonResponse({"status": "started"})
        run_next_step(session)
        return redirect(f"../../{pk}/change/")

    def status_view(self, request, pk):
        """Return current step status as JSON for polling."""
        session = PipelineSession.objects.get(pk=pk)
        return JsonResponse(
            {"current_step": session.current_step, "status": session.status}
        )

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
                session = PipelineSession.objects.create(
                    user=request.user, topic_hint=topic_hint
                )
                change_url = reverse(
                    "admin:app_pipelinesession_change", args=[session.pk]
                )
                return redirect(change_url)
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