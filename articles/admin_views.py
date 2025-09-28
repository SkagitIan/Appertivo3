from __future__ import annotations

from typing import Callable, Iterable

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.views.decorators.http import require_POST


from .models import ArticleRun, PromptTemplate
from .pipeline import PIPELINE_STEPS, launch_article_run

def extend_admin_urls(original_get_urls: Callable[[], Iterable]) -> Callable[[], Iterable]:
    """Attach the articles dashboard to the admin without disrupting defaults."""

    def get_urls():
        urls = list(original_get_urls())
        custom = [
            path(
                "articles/dashboard/",
                admin.site.admin_view(admin_dashboard),
                name="articles_admin_dashboard",
            ),
            path(
                "articles/dashboard/start-run/",
                admin.site.admin_view(start_article_run),
                name="articles_admin_start_run",
            ),
            path(
                "articles/dashboard/run-status/<int:run_id>/",
                admin.site.admin_view(run_status),
                name="articles_admin_run_status",
            ),
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
        "start_run_url": reverse("admin:articles_admin_start_run"),
        "run_status_url_base": _status_url_base(),
    }
    return TemplateResponse(request, "admin/articles/dashboard.html", context)


@staff_member_required
def dashboard_redirect(request):  # pragma: no cover - convenience redirect
    return redirect("admin:articles_admin_dashboard")


def _status_url_base() -> str:
    sample = reverse("admin:articles_admin_run_status", args=[0])
    if sample.endswith("0/"):
        return sample[:-2]
    return sample.rsplit("/", 1)[0] + "/"


def _serialize_run(run: ArticleRun) -> dict:
    step_labels = dict(PromptTemplate.STEP_CHOICES)
    step_map = {step.name: step for step in run.steps.all()}
    steps = []
    for step_name in PIPELINE_STEPS:
        step = step_map.get(step_name)
        status = step.status if step else "pending"
        status_display = step.get_status_display() if step else "Pending"
        steps.append(
            {
                "name": step_name,
                "label": step_labels.get(step_name, step_name.replace("_", " ").title()),
                "status": status,
                "status_display": status_display,
                "is_current": run.current_step == step_name and run.status in {"running", "queued"},
                "has_run": step is not None,
            }
        )
    return {
        "id": run.id,
        "status": run.status,
        "status_display": run.get_status_display(),
        "current_step": run.current_step,
        "error_message": run.error_message or "",
        "steps": steps,
    }


@staff_member_required
@require_POST
def start_article_run(request):
    try:
        run = launch_article_run(request.user)
    except Exception as exc:  # pragma: no cover - defensive runtime fallback
        run = (
            ArticleRun.objects.filter(created_by=request.user).order_by("-id").first()
            or ArticleRun.objects.create(created_by=request.user)
        )
        first_step = run.steps.order_by("started_at").first()
        run.status = "failed"
        run.error_message = str(exc)
        if first_step:
            run.current_step = first_step.name
            run.can_resume_from_step = True
            first_step.status = "failed"
            first_step.error_message = str(exc)
            first_step.save(update_fields=["status", "error_message"])
        run.save(
            update_fields=["status", "error_message", "current_step", "can_resume_from_step"]
        )
        return JsonResponse({"run": _serialize_run(run)})
    return JsonResponse({"run": _serialize_run(run)})


@staff_member_required
def run_status(request, run_id: int):
    try:
        run = ArticleRun.objects.prefetch_related("steps").get(pk=run_id)
    except ArticleRun.DoesNotExist:
        return JsonResponse({"error": "Run not found."}, status=404)
    return JsonResponse({"run": _serialize_run(run)})
