from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from django.utils import timezone

from .models import PromptTemplate, RunStep
from .openai_helpers import (
    calculate_nano_cost_cents,
    extract_output_text,
    get_openai_client,
    parse_structured_payload,
)
from .pipeline import build_next_input, finalize_run, next_step_name, schedule_step
from .schemas import RESEARCH_RESPONSE_SCHEMA
from .utils import apply_usage_cost, ensure_dict, ensure_list, sections_to_markdown

logger = logging.getLogger(__name__)


def run_step(step_id: int, *, client: Optional[Any] = None) -> None:
    step = RunStep.objects.select_related("run").get(id=step_id)
    run = step.run
    if run.status == "canceled":
        logger.info("Run %s canceled before step %s", run.pk, step.name)
        return

    step.status = "running"
    step.error_message = ""
    step.save(update_fields=["status", "error_message"])

    run.status = "running"
    run.current_step = step.name
    run.save(update_fields=["status", "current_step"])

    try:
        template = PromptTemplate.objects.get(name=step.name)
    except PromptTemplate.DoesNotExist:
        message = f"Prompt template '{step.name}' not found"
        step.status = "failed"
        step.error_message = message
        step.ended_at = timezone.now()
        step.save(update_fields=["status", "error_message", "ended_at"])
        run.mark_failed(message, step=step)
        return

    client = client or get_openai_client()

    prompt_context = json.dumps(step.input_payload, indent=2, sort_keys=True)
    prompt_text = f"{template.prompt_text.strip()}\n\nContext:\n{prompt_context}"

    try:
        response = client.responses.create(
            model=run.model_info or "gpt-4.1-nano",
            input=prompt_text,
        )
        response_dict = (
            response.model_dump()
            if hasattr(response, "model_dump")
            else getattr(response, "to_dict", lambda: {})()
        )
        output_text = extract_output_text(response)
        parsed_payload = parse_structured_payload(output_text)

        step.status = "ok"
        step.output_payload = parsed_payload
        step.raw_response = response_dict
        step.ended_at = timezone.now()
        step.save(update_fields=["status", "output_payload", "raw_response", "ended_at"])

        usage = getattr(response, "usage", None)
        cost_cents = calculate_nano_cost_cents(usage)
        if cost_cents:
            run.cost_cents += cost_cents
            run.save(update_fields=["cost_cents"])

        next_step = next_step_name(step.name)
        if next_step:
            next_payload = build_next_input(run, next_step)
            new_step = RunStep.objects.create(
                run=run,
                name=next_step,
                input_payload=next_payload,
            )
            schedule_step(new_step)
        else:
            finalize_run(run)
    except Exception as exc:  # pragma: no cover - network failure handling
        logger.exception("Step %s failed: %s", step.name, exc)
        step.status = "failed"
        step.error_message = str(exc)
        step.ended_at = timezone.now()
        step.save(update_fields=["status", "error_message", "ended_at"])
        run.mark_failed(str(exc), step=step)


def generate_research_draft(step_id: int, *, client: Optional[Any] = None) -> None:
    step = RunStep.objects.select_related("run").get(id=step_id)
    run = step.run
    if run.status == "canceled":
        logger.info("Run %s canceled before research", run.pk)
        return

    step.status = "running"
    step.error_message = ""
    step.save(update_fields=["status", "error_message"])

    run.status = "running"
    run.current_step = "draft"
    run.can_resume_from_step = False
    run.save(update_fields=["status", "current_step", "can_resume_from_step"])

    payload = ensure_dict(step.input_payload)
    selected_idea = ensure_dict(payload.get("selected"))
    idea_index = payload.get("idea_index", 0)
    context_details = ensure_dict(payload.get("context"))

    client = client or get_openai_client()
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

    try:
        response = client.responses.create(
            model=run.model_info or "gpt-4.1-nano",
            input=prompt,
            tools=[{"type": "web_search"}],
            response_format={"type": "json_schema", "json_schema": RESEARCH_RESPONSE_SCHEMA},
        )
        response_dict = (
            response.model_dump()
            if hasattr(response, "model_dump")
            else getattr(response, "to_dict", lambda: {})()
        )
        parsed_payload = parse_structured_payload(extract_output_text(response))
        citations = ensure_list(parsed_payload.get("citations"))
        draft_data = ensure_dict(parsed_payload.get("draft"))
        draft_markdown = (
            parsed_payload.get("draft_markdown")
            or draft_data.get("markdown")
            or ""
        )
        if not draft_markdown:
            sections = draft_data.get("sections")
            if sections:
                draft_markdown = sections_to_markdown(sections)
            elif draft_data.get("text"):
                draft_markdown = str(draft_data.get("text"))

        step.status = "ok"
        step.output_payload = {
            "summary": parsed_payload.get("summary", ""),
            "citations": citations,
            "draft": draft_data,
            "draft_markdown": draft_markdown,
            "idea_index": idea_index,
            "title": draft_data.get("title", selected_idea.get("title", "")),
        }
        step.raw_response = response_dict
        step.ended_at = timezone.now()
        step.save(update_fields=["status", "output_payload", "raw_response", "ended_at"])

        usage = getattr(response, "usage", None) or response_dict.get("usage")
        apply_usage_cost(run, usage)
    except Exception as exc:  # pragma: no cover - network failure handling
        logger.exception("Draft research step failed: %s", exc)
        step.status = "failed"
        step.error_message = str(exc)
        step.ended_at = timezone.now()
        step.save(update_fields=["status", "error_message", "ended_at"])
        run.mark_failed(str(exc), step=step)
