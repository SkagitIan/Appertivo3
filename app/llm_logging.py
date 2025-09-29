"""Helpers for recording LLM API usage and estimated costs."""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional

from django.conf import settings

from . import models

logger = logging.getLogger(__name__)


def _per_million_to_per_1k(dollars_per_million: str) -> Decimal:
    """Convert a per-million dollar amount to a per-thousand Decimal."""

    return Decimal(dollars_per_million) / Decimal("1000")


DEFAULT_PRICING: Dict[str, Dict[str, Dict[str, Decimal]]] = {
    "openai": {
        "gpt-5": {
            "input_per_1k": _per_million_to_per_1k("1.25"),
            "cached_input_per_1k": _per_million_to_per_1k("0.125"),
            "output_per_1k": _per_million_to_per_1k("10.00"),
        },
        "gpt-5-mini": {
            "input_per_1k": _per_million_to_per_1k("0.25"),
            "cached_input_per_1k": _per_million_to_per_1k("0.025"),
            "output_per_1k": _per_million_to_per_1k("2.00"),
        },
        "gpt-5-nano": {
            "input_per_1k": _per_million_to_per_1k("0.05"),
            "cached_input_per_1k": _per_million_to_per_1k("0.005"),
            "output_per_1k": _per_million_to_per_1k("0.40"),
        },
        "gpt-5-chat-latest": {
            "input_per_1k": _per_million_to_per_1k("1.25"),
            "cached_input_per_1k": _per_million_to_per_1k("0.125"),
            "output_per_1k": _per_million_to_per_1k("10.00"),
        },
        "gpt-5-codex": {
            "input_per_1k": _per_million_to_per_1k("1.25"),
            "cached_input_per_1k": _per_million_to_per_1k("0.125"),
            "output_per_1k": _per_million_to_per_1k("10.00"),
        },
        "gpt-4.1": {
            "input_per_1k": _per_million_to_per_1k("2.00"),
            "cached_input_per_1k": _per_million_to_per_1k("0.50"),
            "output_per_1k": _per_million_to_per_1k("8.00"),
        },
        "gpt-4.1-mini": {
            "input_per_1k": _per_million_to_per_1k("0.40"),
            "cached_input_per_1k": _per_million_to_per_1k("0.10"),
            "output_per_1k": _per_million_to_per_1k("1.60"),
        },
        "gpt-4.1-nano": {
            "input_per_1k": _per_million_to_per_1k("0.10"),
            "cached_input_per_1k": _per_million_to_per_1k("0.025"),
            "output_per_1k": _per_million_to_per_1k("0.40"),
        },
        "gpt-4o": {
            "input_per_1k": _per_million_to_per_1k("2.50"),
            "cached_input_per_1k": _per_million_to_per_1k("1.25"),
            "output_per_1k": _per_million_to_per_1k("10.00"),
        },
        "gpt-4o-2024-05-13": {
            "input_per_1k": _per_million_to_per_1k("5.00"),
            "output_per_1k": _per_million_to_per_1k("15.00"),
        },
        "gpt-4o-mini": {
            "input_per_1k": _per_million_to_per_1k("0.15"),
            "cached_input_per_1k": _per_million_to_per_1k("0.075"),
            "output_per_1k": _per_million_to_per_1k("0.60"),
        },
        "gpt-realtime": {
            "input_per_1k": _per_million_to_per_1k("4.00"),
            "cached_input_per_1k": _per_million_to_per_1k("0.40"),
            "output_per_1k": _per_million_to_per_1k("16.00"),
        },
        "gpt-4o-realtime-preview": {
            "input_per_1k": _per_million_to_per_1k("5.00"),
            "cached_input_per_1k": _per_million_to_per_1k("2.50"),
            "output_per_1k": _per_million_to_per_1k("20.00"),
        },
        "gpt-4o-mini-realtime-preview": {
            "input_per_1k": _per_million_to_per_1k("0.60"),
            "cached_input_per_1k": _per_million_to_per_1k("0.30"),
            "output_per_1k": _per_million_to_per_1k("2.40"),
        },
        "gpt-audio": {
            "input_per_1k": _per_million_to_per_1k("2.50"),
            "output_per_1k": _per_million_to_per_1k("10.00"),
        },
        "gpt-4o-audio-preview": {
            "input_per_1k": _per_million_to_per_1k("2.50"),
            "output_per_1k": _per_million_to_per_1k("10.00"),
        },
        "gpt-4o-mini-audio-preview": {
            "input_per_1k": _per_million_to_per_1k("0.15"),
            "output_per_1k": _per_million_to_per_1k("0.60"),
        },
        "o1": {
            "input_per_1k": _per_million_to_per_1k("15.00"),
            "cached_input_per_1k": _per_million_to_per_1k("7.50"),
            "output_per_1k": _per_million_to_per_1k("60.00"),
        },
        "o1-pro": {
            "input_per_1k": _per_million_to_per_1k("150.00"),
            "output_per_1k": _per_million_to_per_1k("600.00"),
        },
        "o3-pro": {
            "input_per_1k": _per_million_to_per_1k("20.00"),
            "output_per_1k": _per_million_to_per_1k("80.00"),
        },
        "o3": {
            "input_per_1k": _per_million_to_per_1k("2.00"),
            "cached_input_per_1k": _per_million_to_per_1k("0.50"),
            "output_per_1k": _per_million_to_per_1k("8.00"),
        },
        "o3-deep-research": {
            "input_per_1k": _per_million_to_per_1k("10.00"),
            "cached_input_per_1k": _per_million_to_per_1k("2.50"),
            "output_per_1k": _per_million_to_per_1k("40.00"),
        },
        "o4-mini": {
            "input_per_1k": _per_million_to_per_1k("1.10"),
            "cached_input_per_1k": _per_million_to_per_1k("0.275"),
            "output_per_1k": _per_million_to_per_1k("4.40"),
        },
        "o4-mini-deep-research": {
            "input_per_1k": _per_million_to_per_1k("2.00"),
            "cached_input_per_1k": _per_million_to_per_1k("0.50"),
            "output_per_1k": _per_million_to_per_1k("8.00"),
        },
        "o3-mini": {
            "input_per_1k": _per_million_to_per_1k("1.10"),
            "cached_input_per_1k": _per_million_to_per_1k("0.55"),
            "output_per_1k": _per_million_to_per_1k("4.40"),
        },
        "o1-mini": {
            "input_per_1k": _per_million_to_per_1k("1.10"),
            "cached_input_per_1k": _per_million_to_per_1k("0.55"),
            "output_per_1k": _per_million_to_per_1k("4.40"),
        },
        "codex-mini-latest": {
            "input_per_1k": _per_million_to_per_1k("1.50"),
            "cached_input_per_1k": _per_million_to_per_1k("0.375"),
            "output_per_1k": _per_million_to_per_1k("6.00"),
        },
        "gpt-4o-mini-search-preview": {
            "input_per_1k": _per_million_to_per_1k("0.15"),
            "output_per_1k": _per_million_to_per_1k("0.60"),
        },
        "gpt-4o-search-preview": {
            "input_per_1k": _per_million_to_per_1k("2.50"),
            "output_per_1k": _per_million_to_per_1k("10.00"),
        },
        "computer-use-preview": {
            "input_per_1k": _per_million_to_per_1k("3.00"),
            "output_per_1k": _per_million_to_per_1k("12.00"),
        },
        "gpt-image-1": {
            "input_per_1k": _per_million_to_per_1k("5.00"),
            "cached_input_per_1k": _per_million_to_per_1k("1.25"),
        },
        "dall-e-3": {"per_call": Decimal("0.04000")},
    },
    "gemini": {
        "gemini-2.5-flash-image-preview": {"per_call": Decimal("0.00250")},
    },
}


def _get_pricing() -> Dict[str, Dict[str, Dict[str, Decimal]]]:
    """Return the configured pricing table."""

    configured = getattr(settings, "LLM_PRICING", None)
    if not configured:
        return DEFAULT_PRICING
    pricing: Dict[str, Dict[str, Dict[str, Decimal]]] = {}
    for provider, models_config in configured.items():
        pricing[provider] = {}
        for model_name, values in models_config.items():
            pricing[provider][model_name] = {}
            for key, value in values.items():
                try:
                    pricing[provider][model_name][key] = Decimal(str(value))
                except Exception:  # pragma: no cover - defensive
                    logger.debug("Skipping invalid pricing value for %s/%s", provider, model_name)
    return pricing


def _safe_int(value: Any) -> Optional[int]:
    """Return a safe integer representation."""

    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


def _safe_decimal(value: Any) -> Optional[Decimal]:
    """Return a Decimal for numeric values."""

    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:  # pragma: no cover - defensive
        return None


def _usage_to_dict(usage: Any) -> Dict[str, Any]:
    """Convert a usage namespace to a JSON-serialisable dict."""

    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    data: Dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens", "total_cost"):
        if hasattr(usage, key):
            value = getattr(usage, key)
            if isinstance(value, (int, float, str)):
                data[key] = value
            else:
                data[key] = str(value)
    return data


def _ensure_iterable(value: Any) -> Iterable[Any]:
    """Return an iterable view of the value."""

    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return value
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (str, bytes)):
        return [value]
    if hasattr(value, "__iter__"):
        return list(value)
    return [value]


def _collect_text_segments(value: Any) -> List[str]:
    """Walk nested OpenAI response data and gather text segments."""

    segments: List[str] = []
    for item in _ensure_iterable(value):
        if isinstance(item, (str, bytes)):
            if item:
                segments.append(str(item))
            continue

        text = getattr(item, "text", None)
        if isinstance(text, (str, bytes)):
            if text:
                segments.append(str(text))

        if isinstance(item, dict):
            if isinstance(item.get("text"), (str, bytes)):
                segments.append(str(item["text"]))
            if "content" in item:
                segments.extend(_collect_text_segments(item["content"]))
            continue

        content = getattr(item, "content", None)
        if content is not None:
            segments.extend(_collect_text_segments(content))

    return segments


def _extract_response_text(value: Any) -> Optional[str]:
    """Return joined text segments from a response field."""

    segments = [segment.strip() for segment in _collect_text_segments(value) if segment.strip()]
    if not segments:
        return None
    return "\n".join(segments)


def _estimate_cost_cents(
    provider: str,
    model_name: str,
    call_type: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
) -> int:
    """Estimate call cost in cents using configured pricing."""

    pricing = _get_pricing()
    model_pricing = pricing.get(provider, {}).get(model_name) or pricing.get(provider, {}).get("__default__", {})
    total = Decimal("0")

    if call_type == models.LlmCallLog.CallType.IMAGE:
        per_call = model_pricing.get("per_call") or model_pricing.get("per_image")
        if per_call:
            total = per_call
    else:
        input_rate = model_pricing.get("input_per_1k", Decimal("0"))
        output_rate = model_pricing.get("output_per_1k", Decimal("0"))
        if input_tokens:
            total += Decimal(input_tokens) / Decimal(1000) * input_rate
        if output_tokens:
            total += Decimal(output_tokens) / Decimal(1000) * output_rate

    cents = int((total * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return max(cents, 0)


def log_llm_call(
    *,
    user=None,
    provider: str,
    model_name: str,
    call_type: str,
    step: str,
    function_name: str,
    response: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
) -> None:
    """Persist usage metrics for an LLM call."""

    if not getattr(settings, "LLM_COST_TRACKING_ENABLED", True):
        return

    usage = getattr(response, "usage", None)
    if input_tokens is None and usage is not None:
        input_tokens = _safe_int(getattr(usage, "input_tokens", None))
    if output_tokens is None and usage is not None:
        output_tokens = _safe_int(getattr(usage, "output_tokens", None))
    if total_tokens is None and usage is not None:
        total_tokens = _safe_int(getattr(usage, "total_tokens", None))

    explicit_cost = _safe_decimal(getattr(usage, "total_cost", None)) if usage is not None else None
    if explicit_cost is not None:
        cost_cents = int((explicit_cost * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    else:
        cost_cents = _estimate_cost_cents(provider, model_name, call_type, input_tokens, output_tokens)

    payload = metadata.copy() if metadata else {}
    usage_dict = _usage_to_dict(usage)
    if usage_dict:
        payload.setdefault("usage", usage_dict)
    if response is not None and getattr(response, "id", None):
        payload.setdefault("response_id", getattr(response, "id"))

    response_input = _extract_response_text(getattr(response, "input", None)) if response else None
    if response_input:
        payload.setdefault("response_input_text", response_input)

    response_output = _extract_response_text(getattr(response, "output", None)) if response else None
    if response_output:
        payload.setdefault("response_output_text", response_output)

    try:
        models.LlmCallLog.objects.create(
            user=user if getattr(user, "pk", None) else None,
            provider=provider,
            model_name=model_name,
            call_type=call_type,
            step=step,
            function_name=function_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_cents=cost_cents,
            metadata=payload,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Failed to log LLM call for %s/%s: %s", provider, model_name, exc)
