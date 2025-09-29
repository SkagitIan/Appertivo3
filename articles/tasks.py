from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from django.utils import timezone

try:  # pragma: no cover - optional import guard for tests
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled during CI without openai installed
    OpenAI = None  # type: ignore

from .models import PromptTemplate, RunStep
from .pipeline import build_next_input, finalize_run, next_step_name, schedule_step

logger = logging.getLogger(__name__)


def get_openai_client():
    if OpenAI is None:
        raise RuntimeError("openai package is not installed")
    return OpenAI()


def _extract_text(response: Any) -> str:
    if response is None:
        return ""
    text = getattr(response, "output_text", None)
    if text:
        return text
    output = getattr(response, "output", None)
    if output and isinstance(output, list):
        texts = []
        for item in output:
            content = item.get("content") if isinstance(item, dict) else None
            if isinstance(content, list):
                for piece in content:
                    if isinstance(piece, dict) and piece.get("type") == "output_text":
                        texts.append(piece.get("text", ""))
            elif isinstance(content, str):
                texts.append(content)
        return "\n".join(filter(None, texts))
    if hasattr(response, "model_dump_json"):
        try:
            data = json.loads(response.model_dump_json())
            return data.get("output_text", "")
        except Exception:  # pragma: no cover - defensive
            return ""
    return ""


def _parse_payload(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if not cleaned:
        return {}
    # Allow fenced code blocks from the model output.
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "json" in cleaned.splitlines()[0]:
            cleaned = "\n".join(cleaned.splitlines()[1:])
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {"text": text}


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
        output_text = _extract_text(response)
        parsed_payload = _parse_payload(output_text)

        step.status = "ok"
        step.output_payload = parsed_payload
        step.raw_response = response_dict
        step.ended_at = timezone.now()
        step.save(update_fields=["status", "output_payload", "raw_response", "ended_at"])

        usage = getattr(response, "usage", None)
        if usage and hasattr(usage, "total_tokens"):
            total_tokens = usage.total_tokens  # type: ignore[attr-defined]
            run.cost_cents += int(total_tokens or 0)
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
