from __future__ import annotations

import json
from typing import Dict, Optional

from django.db import transaction
"""Pipeline helpers for the automated multi-step flow.

This module schedules background work using Celery tasks. We keep a small
indirection so callers do not need to know the queueing backend.
"""

from .models import Article, ArticleRun, RunStep

PIPELINE_STEPS = ["ideas", "scoring", "outline", "draft", "polish", "seo"]


def next_step_name(current: str) -> Optional[str]:
    try:
        index = PIPELINE_STEPS.index(current)
    except ValueError:
        return None
    if index + 1 < len(PIPELINE_STEPS):
        return PIPELINE_STEPS[index + 1]
    return None


def schedule_step(step: RunStep) -> None:
    # Use Celery to run the step asynchronously.
    # We import inside the function to avoid circular imports at module load.
    from .tasks import run_pipeline_step_task

    run_pipeline_step_task.delay(step.id)


def launch_article_run(user, *, topic: str = "independent restaurant operations") -> ArticleRun:
    run = ArticleRun.objects.create(created_by=user, status="queued", current_step=None)
    first_step = RunStep.objects.create(
        run=run,
        name=PIPELINE_STEPS[0],
        input_payload={"topic": topic},
    )
    schedule_step(first_step)
    return run


def get_step_output(run: ArticleRun, name: str) -> Dict:
    try:
        step = run.steps.get(name=name, status="ok")
    except RunStep.DoesNotExist:
        return {}
    payload = step.output_payload or {}
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {}
    return payload


def build_next_input(run: ArticleRun, next_step: str) -> Dict:
    ideas = get_step_output(run, "ideas")
    scoring = get_step_output(run, "scoring")
    outline = get_step_output(run, "outline")
    draft = get_step_output(run, "draft")
    polish = get_step_output(run, "polish")

    if next_step == "scoring":
        return {
            "ideas": ideas.get("ideas"),
            "notes": ideas.get("notes"),
        }
    if next_step == "outline":
        return {
            "winner": scoring.get("winner"),
        }
    if next_step == "draft":
        return {
            "outline": outline.get("outline"),
            "sources": outline.get("sources"),
            "winner": scoring.get("winner"),
        }
    if next_step == "polish":
        return {
            "sections": draft.get("sections"),
            "winner": scoring.get("winner"),
        }
    if next_step == "seo":
        return {
            "winner": scoring.get("winner"),
            "outline": outline.get("outline"),
            "draft": polish or draft,
        }
    return {}


def _sections_to_markdown(sections) -> str:
    if not isinstance(sections, list):
        return ""
    lines = []
    for section in sections:
        heading = section.get("h2") if isinstance(section, dict) else None
        if heading:
            lines.append(f"## {heading}".strip())
        paragraphs = section.get("paragraphs") if isinstance(section, dict) else None
        if isinstance(paragraphs, list):
            for paragraph in paragraphs:
                if paragraph:
                    lines.append(str(paragraph).strip())
        lines.append("")
    return "\n".join(line for line in lines if line is not None).strip()


def finalize_run(run: ArticleRun) -> Article:
    scoring = get_step_output(run, "scoring")
    outline = get_step_output(run, "outline")
    draft = get_step_output(run, "draft")
    polish = get_step_output(run, "polish")
    seo = get_step_output(run, "seo")

    winner = scoring.get("winner", {}) if isinstance(scoring, dict) else {}
    sections = polish.get("sections") if isinstance(polish, dict) else None
    if not sections:
        sections = draft.get("sections") if isinstance(draft, dict) else None

    body_markdown = ""
    if sections:
        body_markdown = _sections_to_markdown(sections)
    elif isinstance(polish, dict):
        body_markdown = polish.get("text", "")
    elif isinstance(draft, dict):
        body_markdown = draft.get("text", "")
    summary = winner.get("summary") or scoring.get("summary") or ""

    article_defaults = {
        "summary": summary or "",
        "outline_json": outline.get("outline") if isinstance(outline, dict) else outline,
        "body_markdown": body_markdown or "",
        "sources_json": outline.get("sources") if isinstance(outline, dict) else [],
        "seo_title": seo.get("seo_title", "") if isinstance(seo, dict) else "",
        "seo_description": seo.get("seo_description", "") if isinstance(seo, dict) else "",
        "og_image_url": seo.get("og_image_url") if isinstance(seo, dict) else None,
        "slug": seo.get("slug") if isinstance(seo, dict) else "",
    }

    title = seo.get("seo_title") if isinstance(seo, dict) and seo.get("seo_title") else winner.get("title")
    if not title:
        title = "Independent Restaurant Insights"

    with transaction.atomic():
        article, _created = Article.objects.update_or_create(
            run=run,
            defaults={
                "title": title,
                **article_defaults,
                "status": "draft",
            },
        )
        run.status = "completed"
        run.current_step = None
        run.error_message = ""
        run.can_resume_from_step = False
        run.save(update_fields=["status", "current_step", "error_message", "can_resume_from_step"])
    return article
