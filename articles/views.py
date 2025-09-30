from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from django import forms
from django.contrib.admin.views.decorators import staff_member_required
from django.core.validators import FileExtensionValidator
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django_q.tasks import async_task

from .models import Article, ArticleRun, RunStep
from .openai_helpers import extract_output_text, get_openai_client, parse_structured_payload
from .pdf_utils import extract_pdf_text
from .schemas import RESEARCH_RESPONSE_SCHEMA
from .utils import apply_usage_cost, ensure_dict, ensure_list, sections_to_markdown

class ArticleConceptForm(forms.Form):
    topic = forms.CharField(
        label="Working focus",
        required=False,
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Example: Sustainability in independent restaurants",
                "class": "rounded-xl border border-slate-200 px-3 py-2",
            }
        ),
        help_text="Optional working angle for the article concepts.",
    )
    context = forms.CharField(
        label="Research context",
        required=False,
        widget=forms.Textarea(attrs={"rows": 6, "class": "rounded-xl border border-slate-200 p-3"}),
        help_text="Optional unless you upload a PDF — add quick notes to ground the concepts.",
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
    }
    if not run:
        return details

    draft_step = run.steps.filter(name="draft").first()
    details["draft_status"] = draft_step.status if draft_step else None
    details["draft_error"] = (draft_step.error_message or "") if draft_step else ""
    details["draft_pending"] = draft_step.status in {"queued", "running"} if draft_step else False

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
            }
        )

    return details


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
        "run_cost": (active_run.cost_cents or 0) / 100 if active_run else 0,
    }
    return render(request, "articles/staff_dashboard.html", context)


def _render_partial(request, template: str, context: Dict[str, Any], *, status: int = 200) -> HttpResponse:
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

    topic = form.cleaned_data.get("topic") or "Independent restaurant operations"
    context_text = form.cleaned_data["context"]
    pdf_upload = form.cleaned_data.get("pdf_upload")
    pdf_text = extract_pdf_text(pdf_upload) if pdf_upload else ""

    client = get_openai_client()
    prompt = (
        "You are an editorial strategist for independent restaurants. "
        "Provide five distinct article concepts with a compelling title and subtitle. "
        "Return JSON with an 'ideas' list where each idea has 'title', 'subtitle', and 'angle'. "
        "Use the research context and any extracted PDF insights to ground the suggestions."
        f"\n\nContext:\n{context_text}\n\n"
    )
    if pdf_text:
        prompt += f"Extracted PDF notes:\n{pdf_text}\n\n"
    prompt += f"Working focus: {topic}"
    response = client.responses.create(model="gpt-4.1-nano", input=prompt)
    response_dict = (
        response.model_dump() if hasattr(response, "model_dump") else getattr(response, "to_dict", lambda: {})()
    )
    payload = parse_structured_payload(extract_output_text(response))
    ideas = ensure_list(payload.get("ideas"))
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
        "topic": topic,
        "context": context_text,
        "pdf_context": pdf_text,
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
        "ideas_payload": run_payload,
    }
    response = _render_partial(request, "articles/_concept_results.html", context)
    response["HX-Trigger"] = json.dumps({"articles:refresh-runs": True})
    return response


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

    client = get_openai_client()
    prompt = (
        "You are a research assistant with access to live web search. "
        "Given an article concept and research context, compile supporting citations "
        "and draft a structured outline with section headings and bullet paragraphs. "
        "Return structured JSON with keys: summary, citations (list with title, url, and snippet), draft (with title, sections)."
        f"\n\nSelected concept: {json.dumps(selected_idea, ensure_ascii=False)}"
        f"\n\nContext notes: {context_details.get('context', '')}"
        f"\n\nExtracted PDF notes: {context_details.get('pdf_context', '')}"
        f"\n\nTopic focus: {context_details.get('topic', '')}"
    )
    response = client.responses.create(
        model="gpt-5",
        input=prompt,
        tools=[{"type": "web_search"}],
        text={"format": RESEARCH_RESPONSE_SCHEMA},
    )
    response_dict = (
        response.model_dump() if hasattr(response, "model_dump") else getattr(response, "to_dict", lambda: {})()
    )
    payload = parse_structured_payload(extract_output_text(response))
    citations = _ensure_list(payload.get("citations"))
    draft_data = _ensure_dict(payload.get("draft"))
    draft_markdown = payload.get("draft_markdown") or draft_data.get("markdown") or ""
    if not draft_markdown:
        sections = draft_data.get("sections")
        if sections:
            draft_markdown = _sections_to_markdown(sections)
        elif draft_data.get("text"):
            draft_markdown = str(draft_data.get("text"))

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

    async_task("articles.tasks.generate_research_draft", draft_step.id)

    run.refresh_from_db()
    draft_step.refresh_from_db()
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
    }
    response = _render_partial(request, "articles/_draft_workflow.html", context)
    triggers: Dict[str, Any] = {}
    if draft_details["draft_status"] == "ok":
        triggers["articles:refresh-runs"] = True
    if triggers:
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

    client = get_openai_client()
    prompt = (
        "You are a senior editor. Take the refined draft below, preserve approved citations, "
        "and produce a polished markdown article ready for publication with SEO metadata. "
        "Return JSON with keys: title, seo_title, seo_description, summary, body_markdown, sources (list)."
        f"\n\nSelected concept: {json.dumps(selected_idea, ensure_ascii=False)}"
        f"\n\nEditor summary: {summary}"
        f"\n\nCitations: {json.dumps(citations, ensure_ascii=False)}"
        f"\n\nDraft title: {draft_title}\nDraft body:\n{draft_body}"
    )
    response = client.responses.create(model=run.model_info or "gpt-4.1-nano", input=prompt)
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
    response["HX-Trigger"] = json.dumps({"articles:refresh-dashboard": True})
    return response
