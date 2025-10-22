from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Tuple
import uuid
import threading

from django import forms
from django.contrib.admin.views.decorators import staff_member_required
from django.core.validators import FileExtensionValidator
from django.core.files.storage import default_storage
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.db.models import Q
from django.views.decorators.http import require_GET, require_POST
try:
    from django_q.tasks import async_task as q_async_task  # test compatibility shim
except Exception:  # pragma: no cover - fallback when django_q is unavailable
    def q_async_task(*args, **kwargs):  # type: ignore
        return None

from .models import Article, ArticleRun, RunStep, ArticleIdea
from .openai_helpers import extract_output_text, get_openai_client, parse_structured_payload
from .pdf_utils import extract_pdf_text
from .utils import (
    apply_usage_cost,
    ensure_dict,
    ensure_list,
    sections_to_markdown,
    extract_ideas_from_text,
    save_pdf_upload,
)

logger = logging.getLogger(__name__)


def async_task(func_path: str, *args, **kwargs):
    """Queue adapter for tests and production.

    - In production we prefer Celery tasks.
    - Tests that patch articles.views.async_task continue to work.
    """
    if func_path == "articles.tasks.generate_research_draft":
        try:
            from .tasks import generate_research_draft_task
            return generate_research_draft_task.delay(*args, **kwargs)
        except Exception:
            # As a last resort, fall back to the django_q stub (no-op inline)
            return q_async_task(func_path, *args, **kwargs)
    return q_async_task(func_path, *args, **kwargs)

class ArticleConceptForm(forms.Form):
    context = forms.CharField(
        label="Research context",
        required=False,
        widget=forms.Textarea(attrs={"rows": 6, "class": "rounded-xl border border-slate-200 p-3"}),
        help_text="Paste research notes or a summary of the uploaded PDF so concepts stay on brief.",
    )
    pdf_upload = forms.FileField(
        label="Attach PDF research",
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
        widget=forms.ClearableFileInput(
            attrs={
                "accept": "application/pdf",
                "class": "rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm",
            }
        ),
        help_text="Optional. Uploading research counts as context if you prefer not to type notes.",
    )

    def clean(self) -> Dict[str, Any]:
        cleaned_data = super().clean()
        context_text = (cleaned_data.get("context") or "").strip()
        pdf = cleaned_data.get("pdf_upload")
        if not context_text and not pdf:
            raise forms.ValidationError(
                "Add brief context notes or attach a research PDF so the model has guidance."
            )
        cleaned_data["context"] = context_text
        return cleaned_data


class ArticleConceptChoiceForm(forms.Form):
    run_id = forms.IntegerField()
    idea_index = forms.IntegerField(min_value=0, max_value=20)


class ArticleDraftReviewForm(forms.Form):
    run_id = forms.IntegerField(widget=forms.HiddenInput())
    draft_title = forms.CharField(
        label="Draft title",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "rounded-xl border border-slate-200 px-3 py-2"}),
    )
    draft_body = forms.CharField(
        label="Editable draft",
        widget=forms.Textarea(attrs={"rows": 16, "class": "font-mono text-sm rounded-xl border border-slate-200 p-3"}),
    )
    editor_notes = forms.CharField(
        label="Editor comments for final polish",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "class": "rounded-xl border border-slate-200 p-3 text-sm",
                "placeholder": "Optional notes to shape the polished article…",
            }
        ),
    )


class ArticlePublishForm(forms.Form):
    run_id = forms.IntegerField()
    article_id = forms.IntegerField()


def _get_run_for_request(request, run_id: int) -> ArticleRun:
    return get_object_or_404(
        ArticleRun.objects.prefetch_related("steps"),
        pk=run_id,
        created_by=request.user,
    )


def _gather_run_context(
    run: ArticleRun,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    ideas_step = None
    draft_step = None
    final_step = None
    for step in run.steps.all():
        if step.name == "ideas":
            ideas_step = step
        elif step.name == "draft":
            draft_step = step
        elif step.name == "seo":
            final_step = step
    ideas_payload = ensure_dict(ideas_step.output_payload if ideas_step else {})
    draft_payload = ensure_dict(draft_step.output_payload if draft_step else {})
    final_payload = ensure_dict(final_step.output_payload if final_step else {})
    ideas = ensure_list(ideas_payload.get("ideas"))
    return ideas, ideas_payload, draft_payload, final_payload


def _prepare_draft_details(
    run: ArticleRun | None,
    ideas: List[Dict[str, Any]],
    draft_payload: Dict[str, Any],
) -> Dict[str, Any]:
    details: Dict[str, Any] = {
        "draft_form": None,
        "citations": [],
        "draft_summary": "",
        "selected_index": None,
        "selected_idea": None,
        "draft_status": None,
        "draft_error": "",
        "draft_pending": False,
        "draft_progress": None,
        "draft_stage": None,
    }
    if not run:
        return details

    draft_step = run.steps.filter(name="draft").first()
    details["draft_status"] = draft_step.status if draft_step else None
    details["draft_error"] = (draft_step.error_message or "") if draft_step else ""
    details["draft_pending"] = draft_step.status in {"queued", "running"} if draft_step else False
    if draft_step and details["draft_pending"]:
        progress = ensure_dict(ensure_dict(draft_step.output_payload).get("progress"))
        details["draft_progress"] = progress.get("percent")
        details["draft_stage"] = progress.get("stage")

    citations = ensure_list(draft_payload.get("citations"))
    draft_summary = draft_payload.get("summary", "")
    selected_index = draft_payload.get("idea_index")
    selected_idea = None
    if selected_index is not None and 0 <= selected_index < len(ideas):
        selected_idea = ideas[selected_index]

    draft_markdown = draft_payload.get("draft_markdown") or ""
    draft_data = ensure_dict(draft_payload.get("draft"))
    if not draft_markdown:
        sections = draft_data.get("sections")
        if sections:
            draft_markdown = sections_to_markdown(sections)
        elif draft_data.get("text"):
            draft_markdown = str(draft_data.get("text"))

    draft_title = draft_payload.get("title") or ""
    if not draft_title and selected_idea:
        draft_title = selected_idea.get("title", "")

    details.update(
        {
            "citations": citations,
            "draft_summary": draft_summary,
            "selected_index": selected_index,
            "selected_idea": selected_idea,
        }
    )

    if draft_markdown:
        details["draft_form"] = ArticleDraftReviewForm(
            initial={
                "run_id": run.id,
                "draft_title": draft_title or "",
                "draft_body": draft_markdown,
                "editor_notes": draft_payload.get("editor_notes", ""),
            }
        )

    return details


def article_index(request):
    search_query = (request.GET.get("q") or "").strip()
    base_queryset = (
        Article.objects.filter(status="published", published_at__isnull=False)
        .order_by("-published_at")
        .only(
            "title",
            "summary",
            "slug",
            "published_at",
            "seo_description",
            "og_image_url",
            "body_markdown",
        )
    )

    if search_query:
        base_queryset = base_queryset.filter(
            Q(title__icontains=search_query)
            | Q(summary__icontains=search_query)
            | Q(seo_description__icontains=search_query)
            | Q(body_markdown__icontains=search_query)
        )

    total_results = base_queryset.count()
    articles = list(base_queryset)
    for article in articles:
        absolute_url = request.build_absolute_uri(article.get_absolute_url())
        setattr(article, "absolute_url", absolute_url)

    featured_article = None
    recent_articles: List[Article] = []
    archive_articles: List[Article] = []

    if search_query:
        recent_articles = articles
    else:
        if articles:
            featured_article = articles[0]
            recent_articles = articles[1:4]
            archive_articles = articles[4:]

    return render(
        request,
        "articles/index.html",
        {
            "featured_article": featured_article,
            "recent_articles": recent_articles,
            "archive_articles": archive_articles,
            "search_query": search_query,
            "results_count": total_results,
            "all_articles": articles,
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


@staff_member_required
@require_GET
def staff_dashboard(request):
    concept_form = ArticleConceptForm()
    runs = list(
        ArticleRun.objects.filter(created_by=request.user)
        .prefetch_related("steps")
        .order_by("-created_at")[:10]
    )
    articles_by_run = {
        article.run_id: article
        for article in Article.objects.filter(run__in=runs)
    }
    run_costs = {run.id: (run.cost_cents or 0) / 100 for run in runs}
    runs_with_meta = [
        {
            "run": run,
            "article": articles_by_run.get(run.id),
            "cost": run_costs.get(run.id, 0),
        }
        for run in runs
    ]

    active_run = None
    for run in runs:
        article = articles_by_run.get(run.id)
        if run.status in {"running", "queued", "failed"}:
            active_run = run
            break
        if article and article.status == "published":
            continue
        if run.steps.exists():
            active_run = run
            break

    ideas: List[Dict[str, Any]] = []
    ideas_payload: Dict[str, Any] = {}
    draft_payload: Dict[str, Any] = {}
    final_payload: Dict[str, Any] = {}
    final_article = None

    if active_run:
        ideas, ideas_payload, draft_payload, final_payload = _gather_run_context(active_run)
        final_article = articles_by_run.get(active_run.id)

    draft_details = _prepare_draft_details(active_run, ideas, draft_payload)

    context = {
        "form": concept_form,
        "runs": runs,
        "run_articles": articles_by_run,
        "run_costs": run_costs,
        "runs_with_meta": runs_with_meta,
        "active_run": active_run,
        "ideas": ideas,
        "ideas_payload": ideas_payload,
        "selected_index": draft_details["selected_index"],
        "draft_form": draft_details["draft_form"],
        "draft_payload": draft_payload,
        "citations": draft_details["citations"],
        "draft_summary": draft_details["draft_summary"],
        "final_payload": final_payload,
        "final_article": final_article,
        "selected_idea": draft_details["selected_idea"],
        "draft_status": draft_details["draft_status"],
        "draft_error": draft_details["draft_error"],
        "draft_pending": draft_details["draft_pending"],
        "draft_progress": draft_details["draft_progress"],
        "draft_stage": draft_details["draft_stage"],
        "run_cost": (active_run.cost_cents or 0) / 100 if active_run else 0,
    }
    get_token(request)
    return render(request, "articles/staff_dashboard.html", context)


def _render_partial(request, template: str, context: Dict[str, Any], *, status: int = 200) -> HttpResponse:
    get_token(request)
    response = render(request, template, context, status=status)
    return response


def _create_run_with_ideas(
    request,
    input_payload: Dict[str, Any],
    ideas: List[Dict[str, Any]],
    *,
    model_payload: Dict[str, Any],
    response_dict: Dict[str, Any],
    usage: Any,
) -> ArticleRun:
    run = ArticleRun.objects.create(
        created_by=request.user,
        status="running",
        current_step="ideas",
        model_info="gpt-4.1-nano",
    )
    RunStep.objects.create(
        run=run,
        name="ideas",
        status="ok",
        input_payload=input_payload,
        output_payload={
            "ideas": ideas,
            "notes": model_payload.get("notes", ""),
            "raw": model_payload,
        },
        raw_response=response_dict,
        ended_at=timezone.now(),
    )
    apply_usage_cost(run, usage)
    # Save ideas to library for future reuse
    for idea in ideas:
        try:
            ArticleIdea.objects.get_or_create(
                title=idea.get("title", "Untitled concept")[:255],
                subtitle=idea.get("subtitle", ""),
                angle=idea.get("angle", ""),
                defaults={"created_by": request.user, "source_run": run},
            )
        except Exception:
            # Avoid blocking on library insert errors
            logger.exception("Failed to save idea to library")
    return run


@staff_member_required
@require_POST
def staff_generate_concepts(request):
    form = ArticleConceptForm(request.POST, request.FILES)
    if not form.is_valid():
        return _render_partial(
            request,
            "articles/_concept_results.html",
            {"form_errors": form.errors, "ideas": [], "active_run": None},
            status=400,
        )

    context_text = form.cleaned_data["context"]
    pdf_upload = form.cleaned_data.get("pdf_upload")
    pdf_text = extract_pdf_text(pdf_upload) if pdf_upload else ""
    concept_warnings: List[str] = []
    if pdf_upload and not pdf_text:
        concept_warnings.append("We couldn't read text from the PDF. If it's a scanned document, try an OCR'd version.")

    # Keep PDF/context snippet concise to improve grounding on small models
    max_ctx = 3000
    pdf_snippet = (pdf_text or "")[:max_ctx]
    context_snippet = (context_text or "")[:max_ctx]

    # Avoid duplicating published titles
    existing_titles = list(
        Article.objects.filter(status="published")
        .exclude(title__isnull=True)
        .exclude(title__exact="")
        .values_list("title", flat=True)
    )

    client = get_openai_client()
    prompt = (
        "You are an experienced editorial strategist specializing in independent restaurants, food media, and culinary storytelling. "
        "Your task is to develop five unique article concepts designed to attract attention, demonstrate local expertise, and inspire restaurant audiences.\n\n"
        "Return ONLY valid JSON with this shape: \n"
        "{\n  \"ideas\": [\n    {\n      \"title\": string,\n      \"subtitle\": string,\n      \"angle\": string\n    }\n  ],\n  \"notes\": string\n}\n\n"
        "Grounding rules (strict):\n"
        "- Use the provided Context notes and PDF notes to tailor every idea.\n"
        "- Each idea must include at least one keyword or phrase from the context/PDF in the title or subtitle.\n"
        "- Avoid generic ideas; prefer concrete, context-specific angles.\n\n"
        "Each idea must include: \n"
        "- \"title\": a short, scroll-stopping headline (under 12 words)\n"
        "- \"subtitle\": one-sentence elaboration that teases the story\n"
        "- \"angle\": 2–3 sentences summarizing the editorial approach, ideal audience, and why it’s relevant now\n\n"
        "Guidelines:\n"
        "- Use the provided research and PDF insights to ground your ideas in real context (no generic food writing).\n"
        "- Avoid repetition between ideas — each should serve a distinct editorial purpose.\n"
        "- Favor human tone and industry realism over marketing fluff.\n\n"
        "THE READERS WILL BE:\n- independant restaurant owners\n- chefs\n- restaurant managers\n- creative consultants\n- tech savy restaurant workers.\n\n"
        f"Context notes (snippet):\n{context_snippet}\n\n"
    )
    pdf_url = save_pdf_upload(pdf_upload, request, subdir="idea_pdfs") if pdf_upload else None
    if pdf_url and pdf_snippet:
        prompt += f"Extracted PDF notes (snippet):\n{pdf_snippet}\n\n"
    if existing_titles:
        title_list = "\n".join(f"- {t}" for t in existing_titles[:25])
        prompt += f"Avoid duplicating these published Appertivo article titles:\n{title_list}\n\n"

    try:
        model_name = "gpt-4.1-mini"
        if pdf_url:
            # Try structured input with file attachment; fall back to text prompt on error.
            try:
                response = client.responses.create(
                    model=model_name,
                    input=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": prompt},
                                {"type": "input_file", "file_url": pdf_url},
                            ],
                        }
                    ],
                    temperature=0.7,
                )
            except Exception:
                response = client.responses.create(model=model_name, input=prompt, temperature=0.7)
        else:
            response = client.responses.create(model=model_name, input=prompt, temperature=0.7)
        response_dict = (
            response.model_dump()
            if hasattr(response, "model_dump")
            else getattr(response, "to_dict", lambda: {})()
        )
        logger.info(response_dict)
        raw_text = extract_output_text(response)
        payload = parse_structured_payload(raw_text)
        ideas = ensure_list(payload.get("ideas"))
        if not ideas:
            ideas = extract_ideas_from_text(raw_text)
        normalized_ideas: List[Dict[str, Any]] = []
        for idea in ideas:
            if isinstance(idea, dict):
                normalized_ideas.append(
                    {
                        "title": idea.get("title", "Untitled concept"),
                        "subtitle": idea.get("subtitle", idea.get("summary", "")),
                        "angle": idea.get("angle", ""),
                    }
                )
        run_payload = {
            "context": context_text,
            "pdf_context": pdf_text,
            "pdf_url": pdf_url or "",
        }
        run = _create_run_with_ideas(
            request,
            run_payload,
            normalized_ideas,
            model_payload=payload,
            response_dict=response_dict,
            usage=getattr(response, "usage", None) or response_dict.get("usage"),
        )

        context = {
            "ideas": normalized_ideas,
            "active_run": run,
            "ideas_payload": {"notes": payload.get("notes", "")},
            "concept_warnings": concept_warnings,
        }
        response = _render_partial(request, "articles/_concept_results.html", context)
        response["HX-Trigger"] = json.dumps({"articles:refresh-runs": True})
        return response
    except Exception as exc:
        logger.exception("Failed to generate concepts: %s", exc)
        return _render_partial(
            request,
            "articles/_concept_results.html",
            {"form_errors": {"__all__": ["We couldn’t generate concepts right now. Please try again."]}},
            status=400,
        )


@staff_member_required
@require_POST
def staff_select_concept(request):
    form = ArticleConceptChoiceForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest("Invalid concept selection")

    run = _get_run_for_request(request, form.cleaned_data["run_id"])
    ideas, _ideas_payload, draft_payload, _ = _gather_run_context(run)
    ideas_step = run.steps.filter(name="ideas").first()
    context_details = ensure_dict(ideas_step.input_payload if ideas_step else {})

    idea_index = form.cleaned_data["idea_index"]
    if idea_index >= len(ideas):
        return HttpResponseBadRequest("Concept not found for this run")

    selected_idea = ideas[idea_index]

    step_input = {
        "selected": selected_idea,
        "idea_index": idea_index,
        "context": context_details,
    }

    draft_step, created = RunStep.objects.update_or_create(
        run=run,
        name="draft",
        defaults={
            "status": "queued",
            "input_payload": step_input,
            "output_payload": {},
            "raw_response": {},
            "error_message": "",
            "ended_at": None,
        },
    )
    if not created:
        RunStep.objects.filter(pk=draft_step.pk).update(
            status="queued",
            input_payload=step_input,
            output_payload={},
            raw_response={},
            error_message="",
            ended_at=None,
        )
        draft_step.refresh_from_db()

    run.current_step = "draft"
    run.status = "running"
    run.can_resume_from_step = False
    run.save(update_fields=["current_step", "status", "can_resume_from_step"])

    # Queue via Celery or run inline based on feature flag (for interactivity)
    use_background = os.getenv("ARTICLES_USE_BACKGROUND", "1").lower() not in {"0", "false", "no"}
    if use_background:
        async_task("articles.tasks.generate_research_draft", draft_step.id)
        logger.info("Queued research draft for run %s (idea %s)", run.id, idea_index)
    else:
        try:
            from .tasks import generate_research_draft
            generate_research_draft(draft_step.id)
            logger.info("Completed research draft inline for run %s (idea %s)", run.id, idea_index)
        except Exception as exc:
            logger.exception("Inline research failed for run %s: %s", run.id, exc)

    run = ArticleRun.objects.prefetch_related("steps").get(pk=run.pk)
    ideas, _ideas_payload, draft_payload, _ = _gather_run_context(run)
    draft_details = _prepare_draft_details(run, ideas, draft_payload)

    selected_context = draft_details["selected_idea"] or selected_idea
    selected_index = draft_details["selected_index"]
    if selected_index is None:
        selected_index = idea_index

    context = {
        "run": run,
        "selected": selected_context,
        "citations": draft_details["citations"],
        "idea_index": selected_index,
        "draft_form": draft_details["draft_form"],
        "draft_summary": draft_details["draft_summary"],
        "selected_idea": selected_context,
        "draft_status": draft_details["draft_status"],
        "draft_error": draft_details["draft_error"],
        "draft_pending": draft_details["draft_pending"],
        "draft_progress": draft_details["draft_progress"],
        "draft_stage": draft_details["draft_stage"],
    }
    response = _render_partial(request, "articles/_draft_workflow.html", context)
    triggers = {"articles:refresh-runs": True}
    if draft_details["draft_form"] is None and draft_details["draft_status"] in {"queued", "running"}:
        triggers["articles:watch-draft"] = {"run_id": run.id}
    response["HX-Trigger"] = json.dumps(triggers)
    return response


@staff_member_required
@require_GET
def staff_draft_status(request, run_id: int):
    run = _get_run_for_request(request, run_id)
    ideas, _ideas_payload, draft_payload, _ = _gather_run_context(run)
    draft_details = _prepare_draft_details(run, ideas, draft_payload)
    draft_step = run.steps.filter(name="draft").first()
    selected_context = draft_details["selected_idea"]
    if not selected_context and draft_step:
        selected_context = ensure_dict(draft_step.input_payload).get("selected")
    selected_index = draft_details["selected_index"]
    if selected_index is None and draft_step:
        selected_index = ensure_dict(draft_step.input_payload).get("idea_index")

    # Auto-fallback: if queued too long, trigger inline research in a background thread
    if draft_step and draft_step.status == "queued" and getattr(draft_step, "started_at", None):
        try:
            from django.utils import timezone as _tz
            threshold = int(os.getenv("ARTICLES_AUTO_FALLBACK_SECONDS", "30"))
            if threshold > 0 and (_tz.now() - draft_step.started_at).total_seconds() > threshold:
                # Set to running to avoid duplicate triggers then spawn a thread
                RunStep.objects.filter(pk=draft_step.pk, status="queued").update(status="running")
                def _runner(step_id: int):
                    try:
                        from .tasks import generate_research_draft
                        generate_research_draft(step_id)
                    except Exception:
                        logger.exception("Auto-fallback inline research failed")
                threading.Thread(target=_runner, args=(draft_step.id,), daemon=True).start()
                draft_step.refresh_from_db()
                draft_details["draft_status"] = draft_step.status
        except Exception:
            logger.exception("Auto-fallback check failed")

    context = {
        "run": run,
        "selected": selected_context,
        "citations": draft_details["citations"],
        "idea_index": selected_index,
        "draft_form": draft_details["draft_form"],
        "draft_summary": draft_details["draft_summary"],
        "selected_idea": selected_context,
        "draft_status": draft_details["draft_status"],
        "draft_error": draft_details["draft_error"],
        "draft_pending": draft_details["draft_pending"],
        "draft_progress": draft_details["draft_progress"],
        "draft_stage": draft_details["draft_stage"],
    }
    response = _render_partial(request, "articles/_draft_workflow.html", context)
    triggers: Dict[str, Any] = {}
    if draft_details["draft_status"] == "ok":
        triggers["articles:refresh-runs"] = True
    if triggers:
        response["HX-Trigger"] = json.dumps(triggers)
    return response


@staff_member_required
@require_GET
def staff_idea_library(request):
    q = (request.GET.get("q") or "").strip()
    show_archived = (request.GET.get("archived") or "0").lower() in {"1", "true", "yes"}
    ideas_qs = ArticleIdea.objects.filter(created_by=request.user)
    if not show_archived:
        ideas_qs = ideas_qs.filter(archived=False)
    if q:
        from django.db.models import Q as _Q
        ideas_qs = ideas_qs.filter(_Q(title__icontains=q) | _Q(subtitle__icontains=q) | _Q(angle__icontains=q))
    ideas = ideas_qs.order_by("-created_at")[:500]
    return render(request, "articles/idea_library.html", {"ideas": ideas, "query": q, "show_archived": show_archived})


@staff_member_required
@require_POST
def staff_idea_start(request, idea_id: int):
    idea = get_object_or_404(ArticleIdea, pk=idea_id)
    # Create a brand new run with this idea
    run = ArticleRun.objects.create(created_by=request.user, status="running", current_step="draft", model_info="gpt-4.1-nano")
    step_input = {"selected": {"title": idea.title, "subtitle": idea.subtitle, "angle": idea.angle}, "idea_index": 0, "context": {}}
    draft_step = RunStep.objects.create(run=run, name="draft", status="queued", input_payload=step_input)

    # Queue or inline per feature flag
    use_background = os.getenv("ARTICLES_USE_BACKGROUND", "1").lower() not in {"0", "false", "no"}
    if use_background:
        async_task("articles.tasks.generate_research_draft", draft_step.id)
    else:
        from .tasks import generate_research_draft
        generate_research_draft(draft_step.id)

    # Redirect caller straight to the studio with the run focused
    target = f"{reverse('articles:staff_dashboard')}?run={run.id}"
    response = HttpResponse("")
    response.status_code = 204
    response["HX-Redirect"] = target
    return response


@staff_member_required
@require_POST
def staff_idea_archive(request, idea_id: int):
    idea = get_object_or_404(ArticleIdea, pk=idea_id, created_by=request.user)
    idea.archived = not idea.archived
    idea.save(update_fields=["archived"])
    # Redirect back to library to refresh
    response = HttpResponse("")
    response.status_code = 204
    response["HX-Redirect"] = reverse("articles:staff_idea_library")
    return response


@staff_member_required
@require_POST
def staff_idea_delete(request, idea_id: int):
    idea = get_object_or_404(ArticleIdea, pk=idea_id, created_by=request.user)
    idea.delete()
    response = HttpResponse("")
    response.status_code = 204
    response["HX-Redirect"] = reverse("articles:staff_idea_library")
    return response


@staff_member_required
@require_POST
def staff_ideas_bulk_start(request):
    id_list = request.POST.getlist("ids")
    context_text = (request.POST.get("context") or "").strip()
    pdf_upload = request.FILES.get("pdf_upload")
    pdf_url = save_pdf_upload(pdf_upload, request, subdir="idea_pdfs") if pdf_upload else ""

    run_ids: List[int] = []
    use_background = os.getenv("ARTICLES_USE_BACKGROUND", "1").lower() not in {"0", "false", "no"}
    for sid in id_list:
        try:
            idea = ArticleIdea.objects.get(pk=int(sid), created_by=request.user)
        except Exception:
            continue
        run = ArticleRun.objects.create(created_by=request.user, status="running", current_step="draft", model_info="gpt-4.1-nano")
        step_input = {
            "selected": {"title": idea.title, "subtitle": idea.subtitle, "angle": idea.angle},
            "idea_index": 0,
            "context": {"context": context_text, "pdf_url": pdf_url},
        }
        draft_step = RunStep.objects.create(run=run, name="draft", status="queued", input_payload=step_input)
        run_ids.append(run.id)
        if use_background:
            async_task("articles.tasks.generate_research_draft", draft_step.id)
        else:
            from .tasks import generate_research_draft
            generate_research_draft(draft_step.id)

    target = reverse("articles:staff_dashboard")
    if run_ids:
        target = f"{target}?run={run_ids[0]}"
    response = HttpResponse("")
    response.status_code = 204
    response["HX-Redirect"] = target
    return response


@staff_member_required
@require_GET
def staff_simple_dashboard(request):
    """Simplified studio: Upload research → Draft → SEO → Save/Publish.

    This view presents a minimal workflow without concept ideation.
    """
    concept_form = ArticleConceptForm()
    runs = list(
        ArticleRun.objects.filter(created_by=request.user)
        .prefetch_related("steps")
        .order_by("-created_at")[:10]
    )
    articles_by_run = {a.run_id: a for a in Article.objects.filter(run__in=runs)}
    run_costs = {r.id: (r.cost_cents or 0) / 100 for r in runs}
    runs_with_meta = [
        {"run": r, "article": articles_by_run.get(r.id), "cost": run_costs.get(r.id, 0)} for r in runs
    ]

    return render(
        request,
        "articles/simple_dashboard.html",
        {
            "form": concept_form,
            "runs_with_meta": runs_with_meta,
        },
    )


@staff_member_required
@require_POST
def staff_simple_generate(request):
    """Create a run straight from uploaded research/text and start drafting."""
    form = ArticleConceptForm(request.POST, request.FILES)
    if not form.is_valid():
        return _render_partial(
            request,
            "articles/_draft_workflow.html",
            {"form_errors": form.errors},
            status=400,
        )

    context_text = form.cleaned_data["context"]
    pdf_upload = form.cleaned_data.get("pdf_upload")
    pdf_text = extract_pdf_text(pdf_upload) if pdf_upload else ""
    pdf_url = save_pdf_upload(pdf_upload, request, subdir="research_pdfs") if pdf_upload else ""

    # Create a run and seed the draft step directly
    run = ArticleRun.objects.create(
        created_by=request.user,
        status="running",
        current_step="draft",
        model_info="gpt-4.1-nano",
    )
    step_input = {
        "selected": {"title": "Uploaded Research", "subtitle": context_text[:140], "angle": ""},
        "idea_index": 0,
        "context": {"context": context_text, "pdf_url": pdf_url, "pdf_context": pdf_text},
    }
    draft_step = RunStep.objects.create(run=run, name="draft", status="queued", input_payload=step_input)

    use_background = os.getenv("ARTICLES_USE_BACKGROUND", "1").lower() not in {"0", "false", "no"}
    if use_background:
        async_task("articles.tasks.generate_research_draft", draft_step.id)
    else:
        from .tasks import generate_research_draft
        generate_research_draft(draft_step.id)

    run = ArticleRun.objects.prefetch_related("steps").get(pk=run.pk)
    ideas, _ideas_payload, draft_payload, _ = _gather_run_context(run)
    draft_details = _prepare_draft_details(run, ideas, draft_payload)
    selected_context = draft_details["selected_idea"] or step_input.get("selected")

    context = {
        "run": run,
        "selected": selected_context,
        "citations": draft_details["citations"],
        "idea_index": draft_details["selected_index"],
        "draft_form": draft_details["draft_form"],
        "draft_summary": draft_details["draft_summary"],
        "selected_idea": selected_context,
        "draft_status": draft_details["draft_status"],
        "draft_error": draft_details["draft_error"],
        "draft_pending": draft_details["draft_pending"],
        "draft_progress": draft_details["draft_progress"],
        "draft_stage": draft_details["draft_stage"],
    }
    response = _render_partial(request, "articles/_draft_workflow.html", context)
    triggers: Dict[str, Any] = {"articles:refresh-runs": True}
    if draft_details["draft_form"] is None and draft_details["draft_status"] in {"queued", "running"}:
        triggers["articles:watch-draft"] = {"run_id": run.id}
    response["HX-Trigger"] = json.dumps(triggers)
    return response


@staff_member_required
@require_POST
def staff_finalize_article(request):
    form = ArticleDraftReviewForm(request.POST)
    if not form.is_valid():
        return _render_partial(
            request,
            "articles/_draft_workflow.html",
            {"form_errors": form.errors, "draft_form": form},
            status=400,
        )

    run = _get_run_for_request(request, form.cleaned_data["run_id"])
    draft_step = run.steps.filter(name="draft").first()
    if not draft_step:
        return HttpResponseBadRequest("Draft step missing for this run")

    draft_payload = ensure_dict(draft_step.output_payload)
    draft_input = ensure_dict(draft_step.input_payload)
    citations = ensure_list(draft_payload.get("citations"))
    selected_idea = draft_input.get("selected", {})
    summary = draft_payload.get("summary", "")

    draft_body = form.cleaned_data["draft_body"]
    draft_title = form.cleaned_data["draft_title"]
    editor_notes = form.cleaned_data.get("editor_notes", "")

    if editor_notes:
        draft_payload["editor_notes"] = editor_notes
    elif "editor_notes" in draft_payload:
        draft_payload.pop("editor_notes")
    RunStep.objects.filter(pk=draft_step.pk).update(output_payload=draft_payload)

    client = get_openai_client()
    prompt = (
        "You are a senior editor. Take the refined draft below, preserve approved citations, "
        "and produce a polished markdown article ready for publication with SEO metadata. "
        "Return JSON with keys: title, seo_title, seo_description, summary, body_markdown, sources (list)."
        f"\n\nSelected concept: {json.dumps(selected_idea, ensure_ascii=False)}"
        f"\n\nEditor summary: {summary}"
        f"\n\nCitations: {json.dumps(citations, ensure_ascii=False)}"
        f"\n\nEditor comments: {editor_notes}"
        f"\n\nDraft title: {draft_title}\nDraft body:\n{draft_body}"
    )
    response = client.responses.create(model="gpt-4.1-mini", input=prompt)
    response_dict = (
        response.model_dump() if hasattr(response, "model_dump") else getattr(response, "to_dict", lambda: {})()
    )
    payload = parse_structured_payload(extract_output_text(response))
    body_markdown = payload.get("body_markdown") or draft_body
    sources = ensure_list(payload.get("sources")) or citations

    RunStep.objects.update_or_create(
        run=run,
        name="seo",
        defaults={
            "status": "ok",
            "input_payload": {
                "draft": draft_body,
                "title": draft_title,
                "citations": citations,
                "selected": selected_idea,
                "editor_notes": editor_notes,
            },
            "output_payload": payload,
            "raw_response": response_dict,
            "error_message": "",
            "ended_at": timezone.now(),
        },
    )
    apply_usage_cost(run, getattr(response, "usage", None) or response_dict.get("usage"))

    article_defaults = {
        "title": payload.get("title") or draft_title,
        "summary": payload.get("summary", summary),
        "body_markdown": body_markdown,
        "outline_json": draft_payload.get("draft", {}),
        "sources_json": sources,
        "seo_title": payload.get("seo_title", ""),
        "seo_description": payload.get("seo_description", ""),
        "status": "draft",
    }
    article, _created = Article.objects.update_or_create(run=run, defaults=article_defaults)
    run.status = "completed"
    run.current_step = None
    run.save(update_fields=["status", "current_step"])

    context = {
        "article": article,
        "run": run,
        "final_payload": payload,
        "sources": sources,
        "run_cost": (run.cost_cents or 0) / 100,
    }
    response = _render_partial(request, "articles/_final_article_panel.html", context)
    response["HX-Trigger"] = json.dumps({"articles:refresh-runs": True})
    return response


@staff_member_required
@require_POST
def staff_publish_article(request):
    form = ArticlePublishForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest("Invalid publish request")

    run = _get_run_for_request(request, form.cleaned_data["run_id"])
    article = get_object_or_404(Article, pk=form.cleaned_data["article_id"], run=run)
    article.status = "published"
    article.save(update_fields=["status", "published_at"])
    run.status = "completed"
    run.save(update_fields=["status"])

    seo_step = run.steps.filter(name="seo").first()
    final_payload = ensure_dict(seo_step.output_payload) if seo_step else {}
    context = {
        "article": article,
        "run": run,
        "final_payload": final_payload,
        "sources": ensure_list(article.sources_json),
        "run_cost": (run.cost_cents or 0) / 100,
    }
    response = _render_partial(request, "articles/_final_article_panel.html", context)
    response["HX-Trigger"] = json.dumps({"articles:refresh-runs": True})
    return response


@staff_member_required
@require_GET
def staff_runs_fragment(request):
    runs = list(
        ArticleRun.objects.filter(created_by=request.user)
        .prefetch_related("steps")
        .order_by("-created_at")[:10]
    )
    articles_by_run = {
        article.run_id: article
        for article in Article.objects.filter(run__in=runs)
    }
    run_costs = {run.id: (run.cost_cents or 0) / 100 for run in runs}
    runs_with_meta = [
        {
            "run": run,
            "article": articles_by_run.get(run.id),
            "cost": run_costs.get(run.id, 0),
        }
        for run in runs
    ]
    context = {
        "runs": runs,
        "run_articles": articles_by_run,
        "run_costs": run_costs,
        "runs_with_meta": runs_with_meta,
    }
    return render(request, "articles/_run_list.html", context)


@staff_member_required
@require_POST
def staff_delete_run(request, run_id: int):
    run = _get_run_for_request(request, run_id)
    Article.objects.filter(run=run).delete()
    run.delete()

    runs = list(
        ArticleRun.objects.filter(created_by=request.user)
        .prefetch_related("steps")
        .order_by("-created_at")[:10]
    )
    articles_by_run = {
        article.run_id: article for article in Article.objects.filter(run__in=runs)
    }
    run_costs = {run_item.id: (run_item.cost_cents or 0) / 100 for run_item in runs}
    runs_with_meta = [
        {
            "run": run_item,
            "article": articles_by_run.get(run_item.id),
            "cost": run_costs.get(run_item.id, 0),
        }
        for run_item in runs
    ]
    context = {
        "runs": runs,
        "run_articles": articles_by_run,
        "run_costs": run_costs,
        "runs_with_meta": runs_with_meta,
    }
    response = render(request, "articles/_run_list.html", context)
    response["HX-Trigger"] = json.dumps({"articles:refresh-runs": True, "articles:clear-workflow": True})
    return response


@staff_member_required
@require_POST
def staff_cancel_research(request, run_id: int):
    """Allow staff to cancel an in-progress research draft run.

    Marks the run as canceled and the draft step as failed with a user-facing
    message. The UI will refresh and stop polling.
    """
    run = _get_run_for_request(request, run_id)
    draft_step = run.steps.filter(name="draft").first()
    if draft_step and draft_step.status in {"queued", "running"}:
        draft_step.status = "failed"
        draft_step.error_message = "Canceled by user"
        draft_step.ended_at = timezone.now()
        draft_step.save(update_fields=["status", "error_message", "ended_at"])
    run.status = "canceled"
    run.save(update_fields=["status"])

    # Recompute details and return the draft panel so the user can retry
    run = ArticleRun.objects.prefetch_related("steps").get(pk=run.pk)
    ideas, _ideas_payload, draft_payload, _ = _gather_run_context(run)
    draft_details = _prepare_draft_details(run, ideas, draft_payload)
    context = {
        "run": run,
        "selected": draft_details.get("selected_idea"),
        "citations": draft_details.get("citations", []),
        "idea_index": draft_details.get("selected_index"),
        "draft_form": draft_details.get("draft_form"),
        "draft_summary": draft_details.get("draft_summary", ""),
        "selected_idea": draft_details.get("selected_idea"),
        "draft_status": draft_details.get("draft_status"),
        "draft_error": draft_details.get("draft_error"),
        "draft_pending": draft_details.get("draft_pending"),
        "draft_progress": draft_details.get("draft_progress"),
        "draft_stage": draft_details.get("draft_stage"),
    }
    response = _render_partial(request, "articles/_draft_workflow.html", context)
    response["HX-Trigger"] = json.dumps({"articles:refresh-runs": True})
    return response
