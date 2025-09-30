from __future__ import annotations

import json

from typing import Any, Dict, List

from .openai_helpers import calculate_nano_cost_cents


def ensure_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(data, dict):
            return data
    return {}


def ensure_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            return data
    return []


def sections_to_markdown(sections: Any) -> str:
    if not isinstance(sections, list):
        return ""
    lines: List[str] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = section.get("heading") or section.get("h2")
        if heading:
            lines.append(f"## {heading}".strip())
        paragraphs = section.get("paragraphs")
        if isinstance(paragraphs, list):
            for paragraph in paragraphs:
                if paragraph:
                    lines.append(str(paragraph).strip())
        elif section.get("body"):
            lines.append(str(section["body"]).strip())
        lines.append("")
    return "\n".join(line for line in lines if line is not None).strip()


def apply_usage_cost(run, usage: Any) -> None:
    cost_cents = calculate_nano_cost_cents(usage)
    if cost_cents:
        run.cost_cents += cost_cents
        run.save(update_fields=["cost_cents"])
