from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Tuple

from django import forms
from django.contrib.admin.views.decorators import staff_member_required
from django.core.validators import FileExtensionValidator
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django_q.tasks import async_task

from .models import Article, ArticleRun, RunStep
from .openai_helpers import extract_output_text, get_openai_client, parse_structured_payload
from .pdf_utils import extract_pdf_text
from .utils import apply_usage_cost, ensure_dict, ensure_list, sections_to_markdown

logger = logging.getLogger(__name__)

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
                "editor_notes": draft_payload.get("editor_notes", ""),
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

    client = get_openai_client()
    prompt = f"""
        You are an experienced editorial strategist specializing in independent restaurants, food media, and culinary storytelling. 
        Your task is to develop five unique article concepts designed to attract attention, demonstrate local expertise, and inspire restaurant audiences.

        Each idea must include:
        - "title": a short, scroll-stopping headline (under 12 words)
        - "subtitle": one-sentence elaboration that teases the story
        - "angle": 2–3 sentences summarizing the editorial approach, ideal audience, and why it’s relevant now

        Guidelines:
        - Use the provided research and PDF insights to ground your ideas in real context (no generic food writing).
        - Avoid repetition between ideas — each should serve a distinct editorial purpose.
        - Favor human tone and industry realism over marketing fluff.

        THE READERS WILL BE:
        - independant restaurant owners
        - chefs, 
        - restaurant managers,
        - creative consultants
        - tech savy restaurant workers.

        Context:\n{article_context()}\n\n"""

    if pdf_text:
        prompt += f"Extracted PDF notes:\n{pdf_text}\n\n"
    response = client.responses.create(model="gpt-4.1-nano", input=prompt)
    response_dict = (
        response.model_dump() if hasattr(response, "model_dump") else getattr(response, "to_dict", lambda: {})()
    )
    logger.info(response_dict)
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
        "ideas_payload": {"notes": payload.get("notes", "")},
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
    logger.info("Queued research draft for run %s (idea %s)", run.id, idea_index)

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

def article_context():

    context = """

            Here’s the PDF fully converted to clean Markdown:

            ---

            # Independent Restaurants in 2025: Challenges and a New Era of Dining Out

            ---

            ## Core Challenges for Independent Restaurant Owners in 2025

            Independent restaurateurs are navigating an exceptionally challenging landscape in 2025, marked by high costs, labor woes, shifting consumer habits, and other pressures. Unlike large chains with deep resources, independents operate on razor-thin margins and feel these stresses acutely. Below are the key pain points holding back independent operators today:

            ### Rising Food & Operating Costs

            Inflation and supply disruptions have driven up the cost of ingredients, utilities, and supplies, squeezing already thin margins.

            * 76% of independent owners cited increasing food prices as the top trend impacting their business.
            * Nearly all had to raise menu prices in 2024 to keep pace with “rising food, labor, and general operating costs.”
            * Restaurants that raised prices over 15% saw profits drop and customer traffic fall off.
            * After five years of nearly 30% cumulative menu price increases, over half of U.S. adults said they were cutting back on dining out to save money.

            Independents are caught between surging expenses and customers’ price sensitivity.

            ### Labor Shortages and High Wages

            Staffing remains a chronic headache.

            * 92% of independents raised wages in 2024, often by more than 10%.
            * New wage laws and higher pay standards (e.g., California’s FAST Act) raised costs further.
            * Cross-training and flexible schedules are now common retention tools.

            Still, labor costs are among the top two challenges, alongside food costs.

            ### Shifting Consumer Behavior

            Diners are going out less and seeking more value.

            * Over 70% of independent restaurants saw a drop in traffic in 2024.
            * Price sensitivity has grown, and loyalty has eroded.
            * Guests are deal-driven and responsive to promotions or perceived value.

            To adapt, independents are leaning heavily on digital marketing:

            * Nearly 75% use social media as a primary marketing tool.
            * Many invest in loyalty programs, experiences, and personalized outreach to deepen engagement.

            ### Technology Fragmentation and Complexity

            Independent operators struggle with fragmented tech stacks:

            * Many juggle multiple tablets and disconnected systems for delivery, online ordering, and reservations.
            * These inefficiencies cost time and money.
            * “Technology should help reduce complexity and cost, not add to it,” noted one industry veteran.

            Operators increasingly seek **“smarter platforms, fewer vendors.”**
            Unified tools and order aggregators are becoming essential to manage delivery apps and POS systems efficiently.

            ### Competitive and Regulatory Pressures

            * Chain restaurants grew sales by **8.2%** in 2023, while independents grew only **1.5%**.
            * Regulations (minimum wage, scheduling, menu labeling) hit small businesses hardest.
            * Higher interest rates raised loan costs.
            * Extreme weather disrupted supply chains—2024 saw 27 billion-dollar disasters in the U.S.

            Despite this, independent restaurants that **innovate**—testing new revenue models or technology—are outperforming others. Over 85% made at least one non-traditional change in 2024, from pop-ups to product lines.

            ---

            ## Opportunities That Could Redefine Dining Out

            ### 1. Automation and AI to Streamline Operations

            Automation is moving mainstream:

            * 47% of operators plan to rely more on automation.
            * Examples include kitchen robots, AI-driven order assistants, and smart ovens.

            Sweetgreen’s “Infinite Kitchen” saw:

            * 10% higher ticket averages
            * 45% lower turnover
            * Improved accuracy and throughput

            Automation allows small teams to operate efficiently, shifting human focus to hospitality and creativity.

            ### 2. Immersive and Experience-Driven Dining

            Dining is evolving from “meal” to “experience.”

            * 72% of diners want experiential options (chef tables, themed dinners, interactive cooking).
            * 89% cite service and atmosphere as key factors when choosing a restaurant.
            * 64% of full-service patrons now value experience over price.

            Independent restaurants can stand out with:

            * Pop-ups, supper clubs, art/music nights
            * “Storytelling” menus tied to locale or season
            * Multi-sensory dining (light, sound, AR)

            This redefines dining out as an **event** worth leaving home for.

            ### 3. Alternative Business Models and Revenue Streams

            New business models are reshaping the industry:

            * **Ghost kitchens** and **virtual brands** expand reach with minimal overhead.
            * Pop-ups, food halls, and mobile kitchens provide agility and lower fixed costs.
            * 85% of independents tried at least one new revenue stream in 2024.

            Examples:

            * Selling sauces or products retail
            * Hosting ticketed events or classes
            * Subscription-based meal plans

            These flexible formats reduce risk and increase resilience.

            ### 4. Personalization and Digital Engagement

            Data-driven personalization is becoming key:

            * 80% of consumers want brands to personalize offers.
            * Loyalty apps, reservation systems, and AI marketing enable 1:1 connections.
            * Even small restaurants can tailor experiences using POS data and feedback loops.

            Retention is powerful: a **5% increase in repeat customers** can boost profits by **25–95%**.
            AI tools like chatbots and dynamic offers also improve engagement while saving staff time.

            ### 5. Redefining Value Through Innovation

            The next “value revolution” blends **affordability, experience, and ethics**:

            * 47% of operators plan new discounts and deals in 2025.
            * 73% of consumers are willing to change habits for sustainability.

            Independent restaurants can lead with:

            * Bundled meals or prix-fixe experiences
            * Locally sourced, sustainable menus
            * Community engagement and storytelling

            Delivering **more perceived value**—through meaning and experience—could reignite dining demand.

            ---

            ## Conclusion

            Independent restaurant owners face inflation, labor issues, and shifting consumer priorities—but the future isn’t bleak.
            Those who adapt through **innovation, technology, and creativity** are leading the next wave of dining.

            A new era is emerging:

            * Smart automation in the back of house
            * Experiential hospitality up front
            * Personalized, sustainable relationships with guests

            Independent restaurants aren’t just surviving—they’re redefining why people go out to eat.

            ---

            ## Key Sources

            * James Beard Foundation / Deloitte, *2025 Independent Restaurant Industry Report*
            * Restaurant Business Online, *State of Independent Restaurants 2024*
            * Restaurant Dive, *8 Restaurant Trends to Watch in 2025*
            * The Daily Rail, *Top Restaurant Industry Stories of 2024*
            * National Restaurant Association, *2025 State of the Restaurant Industry*
            * Entegra Services, *Future of Dining: Exceptional Experiences*
            * Franchising.com, *Future Restaurant Industry Trends*
            * Modern Restaurant Management, *Research Roundup on Tech*
            * Restaurant Dive, *Sweetgreen’s Robot Kitchens*
            * Multibriefs, *Consumers Want Personalization*

            ---

            Would you like me to format this for your Django CMS (e.g. section headers as HTML or Markdown safe for template injection)?

    """

    return context