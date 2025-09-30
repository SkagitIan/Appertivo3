from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict

try:  # pragma: no cover - optional import guard for tests
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled during CI without openai installed
    OpenAI = None  # type: ignore


NANO_INPUT_RATE_PER_1K = Decimal("0.00015")
NANO_OUTPUT_RATE_PER_1K = Decimal("0.0006")
NANO_FALLBACK_RATE_PER_1K = NANO_INPUT_RATE_PER_1K


def get_openai_client():
    if OpenAI is None:
        raise RuntimeError("openai package is not installed")
    return OpenAI()


def extract_output_text(response: Any) -> str:
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
    dump_json = getattr(response, "model_dump_json", None)
    if callable(dump_json):
        try:
            data = json.loads(dump_json())
            return data.get("output_text", "")
        except Exception:  # pragma: no cover - defensive
            return ""
    return ""


def parse_structured_payload(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    if not cleaned:
        return {}
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("json"):
            cleaned = "\n".join(lines[1:])
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {"text": text}


def calculate_nano_cost_cents(usage: Any) -> int:
    if usage is None:
        return 0

    def _extract(field: str) -> int:
        value = None
        if hasattr(usage, field):
            value = getattr(usage, field)
        elif isinstance(usage, dict):
            value = usage.get(field)
        return int(value or 0)

    input_tokens = _extract("input_tokens")
    output_tokens = _extract("output_tokens")
    total_tokens = _extract("total_tokens")

    cost = Decimal("0")
    if input_tokens or output_tokens:
        cost += (Decimal(input_tokens) / Decimal(1000)) * NANO_INPUT_RATE_PER_1K
        cost += (Decimal(output_tokens) / Decimal(1000)) * NANO_OUTPUT_RATE_PER_1K
    elif total_tokens:
        cost += (Decimal(total_tokens) / Decimal(1000)) * NANO_FALLBACK_RATE_PER_1K

    if not cost:
        return 0

    cents = (cost * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents)
