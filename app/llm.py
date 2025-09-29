"""Helpers for talking to LLM services.

The helpers in this module call out to third party APIs when keys are
configured and otherwise fall back to deterministic placeholder data so
tests can run without network access.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import uuid
from typing import Any, Dict, Optional

import cloudinary
from openai import OpenAI
from replicate.client import Client as ReplicateClient  # type: ignore


from dotenv import load_dotenv
load_dotenv()

from django.core.cache import cache

from . import models

logger = logging.getLogger(__name__)

_openai_api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=_openai_api_key) if _openai_api_key else None
_replicate_token = os.getenv("REPLICATE_API_KEY")
if ReplicateClient and _replicate_token:
    replicate_client = ReplicateClient(api_token=_replicate_token)
else:  # pragma: no cover - fallback when library or token missing
    replicate_client = None
REPLICATE_MODEL = "prunaai/flux.1-dev:b0306d92aa025bb747dc74162f3c27d6ed83798e08e5f8977adf3d859d0536a3"
DEFAULT_IMAGE_URL = "https://placehold.co/800x600?text=Dish"
DEFAULT_CONCEPT_IMAGE_URL = "https://placehold.co/1200x800?text=Concept"
DEFAULT_PRICE_CENTS = 1500
PRICE_CACHE_TTL_SECONDS = 15 * 60

cloudinary.config( 
  cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME"), 
  api_key = os.getenv("CLOUDINARY_API_KEY"), 
  api_secret = os.getenv("CLOUDINARY_SECRET_KEY")
)

def _concept_sketch_prompt(name: str, subtitle: str) -> str:
    """Describe a lightweight concept sketch request for image generation."""

    subtitle_text = subtitle or ""
    return (
        "Create a monochrome pencil sketch that could serve as background art for a "
        "restaurant concept card. Keep the lines clean with minimal shading so the image "
        "stays lightweight, but output it at a high-definition resolution. DO not include any text ever, "
        "no logos, or color.  Should only be of a singular item, not a spread or motif.\n"
        "Ideal image is one that captures the culinary concept, a sketch vignette"
        "should not be of a singular dish but represent a the culiary concept."
        f"Concept name: {name}\n"
        f"Concept subtitle: {subtitle_text}"
    )

def _dish_image_prompt(title: str, description: str) -> str:
    """Return the prompt text for generating a dish image."""

    description_text = description or ""
    prompt = f"""
    "Professional food photography of {title}. {description_text}.
    """


    return prompt


def _summarize_for_logging(value: Any, *, limit: int = 200) -> Any:
    """Return a lightweight summary of Replicate values for logging."""

    if isinstance(value, bytes):
        return f"<bytes length={len(value)}>"

    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + "..."

    if isinstance(value, list):
        summary = [_summarize_for_logging(item, limit=limit) for item in value[:5]]
        if len(value) > 5:
            summary.append("...")
        return summary

    if isinstance(value, dict):
        summary: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 5:
                summary["..."] = f"{len(value) - index} more"
                break
            summary[str(key)] = _summarize_for_logging(item, limit=limit)
        return summary

    try:
        return json.loads(json.dumps(value))
    except Exception:  # pragma: no cover - fallback for unserializable objects
        text = repr(value)
        if len(text) <= limit:
            return text
        return text[:limit] + "..."


def _extract_output_values(output) -> list[bytes | str]:
    """Normalize Replicate output into a list of byte blobs or URLs."""

    values: list[bytes | str] = []
    if output is None:
        return values

    if hasattr(output, "read"):
        try:
            values.append(output.read())
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning("Failed to read Replicate output stream: %s", exc, exc_info=True)
        return values

    if isinstance(output, list):
        for item in output:
            values.extend(_extract_output_values(item))
        return values

    if isinstance(output, dict):
        for item in output.values():
            values.extend(_extract_output_values(item))
        return values

    if isinstance(output, bytes):
        values.append(output)
        return values

    if isinstance(output, str):
        values.append(output)
        return values

    logger.warning("Unexpected Replicate output type: %s", type(output))
    return values


def _generate_replicate_asset(
    prompt: str,
    default_url: str,
    *,
    folder: str,
    output_quality: int,
) -> str:
    """Generate an image via Replicate, upload to Cloudinary, and return the URL."""

    if not replicate_client:
        logger.info(
            "Replicate client unavailable. Returning default asset for folder %s.",
            folder,
        )
        return default_url

    try:
        output = replicate_client.run(
            REPLICATE_MODEL,
            input={
                "prompt": prompt,
                "output_format": "png",
                "output_quality": output_quality,
            },
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("Replicate image generation failed: %s", exc, exc_info=True)
        return default_url

    logger.info(
        "Replicate response received for folder %s: %s",
        folder,
        _summarize_for_logging(output),
    )

    for candidate in _extract_output_values(output):
        try:
            file_obj = candidate
            if isinstance(candidate, bytes):
                file_obj = io.BytesIO(candidate)

            upload_result = cloudinary.uploader.upload(
                file_obj,
                folder=folder,
                public_id=str(uuid.uuid4()),
                overwrite=True,
                resource_type="image",
            )
            optimized_url = cloudinary.CloudinaryImage(upload_result["public_id"]).build_url(
                width=500,
                height=500,
                crop="fill",
                quality="auto",
                fetch_format="auto",
            )
            logger.info(
                "Replicate candidate uploaded for folder %s: %s",
                folder,
                _summarize_for_logging(candidate),
            )
            return optimized_url
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning("Cloudinary upload failed: %s", exc, exc_info=True)

    logger.warning("Replicate output did not yield a usable image.")
    return default_url


def generate_dish_image_from_prompt(prompt: str, default_url: str, *, user=None) -> str:
    """Generate a high quality dish image via Replicate for the provided prompt."""

    return _generate_replicate_asset(
        prompt,
        default_url,
        folder="appertivo/dishes",
        output_quality=95,
    )


def generate_concept_sketch_from_prompt(prompt: str, default_url: str, *, user=None) -> str:
    """Generate a lightweight sketch via Replicate for the provided prompt."""

    return _generate_replicate_asset(
        prompt,
        default_url,
        folder="appertivo/sketches",
        output_quality=60,
    )


def _format_menu_snapshot(restaurant: models.Restaurant) -> Dict[str, Optional[str]]:
    menu_markdown = ""
    if restaurant.active_menu_version and restaurant.active_menu_version.raw_markdown:
        menu_markdown = restaurant.active_menu_version.raw_markdown
    return {
        "restaurant": restaurant.context_json or {},
        "menu_markdown": menu_markdown,
    }


def _menu_snapshot_hash(menu_snapshot: Dict[str, Optional[str]]) -> str:
    """Return a stable hash for the menu snapshot payload."""

    serialized = json.dumps(menu_snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _pricing_cache_key(
    dish: models.DishIdea,
    menu_snapshot_hash: str,
) -> str:
    """Build a cache key for OpenAI price responses."""

    dish_id = getattr(dish, "pk", None) or getattr(dish, "id", None) or "unsaved"
    updated_at = getattr(dish, "updated_at", None)
    updated_value = updated_at.isoformat() if updated_at else "no-updated-at"
    return f"dish-price:{dish_id}:{updated_value}:{menu_snapshot_hash}"


def _call_openai_for_price(
    dish: models.DishIdea,
    menu_snapshot: Dict[str, Optional[str]],
    *,
    menu_snapshot_hash: Optional[str] = None,
    user=None,
) -> Dict[str, Optional[str]]:
    """Return pricing info using OpenAI or deterministic fallback."""

    fallback = {
        "price_cents": DEFAULT_PRICE_CENTS,
        "currency": "USD",
        "rationale": "LLM unavailable, using baseline price.",
    }

    if not client:
        return fallback

    schema = {
        "name": "enhanced_price",
        "type": "json_schema",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "price_cents": {"type": "integer"},
                "currency": {"type": "string"},
                "rationale": {"type": "string"},
            },
            "required": ["price_cents", "currency", "rationale"],
            "additionalProperties": False,
        },
    }

    menu_snapshot_hash = menu_snapshot_hash or _menu_snapshot_hash(menu_snapshot)
    cache_key = _pricing_cache_key(dish, menu_snapshot_hash)

    cached_response = cache.get(cache_key)
    if cached_response is not None:
        return cached_response

    payload = {
        "dish": {
            "title": dish.title,
            "description": dish.description,
            "ingredients": list(dish.ingredient_names or []),
            "categories": list(dish.category_tags or []),
        },
        "restaurant": menu_snapshot.get("restaurant", {}),
        "menu_markdown": menu_snapshot.get("menu_markdown", ""),
    }

    try:
        response = client.responses.create(
            model="gpt-4.1-nano",
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are a pricing analyst for a restaurant. "
                        "Suggest a menu price in cents considering the context, menu, and ingredients."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, indent=2),
                },
            ],
            text={"format": schema},
        )
        raw_text = response.output[0].content[0].text
        data = json.loads(raw_text)
        price_cents = int(data.get("price_cents", fallback["price_cents"]))
        currency = data.get("currency") or fallback["currency"]
        rationale = data.get("rationale") or fallback["rationale"]
        parsed_response = {
            "price_cents": price_cents,
            "currency": currency,
            "rationale": rationale,
        }
        cache.set(cache_key, parsed_response, PRICE_CACHE_TTL_SECONDS)
        return parsed_response
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("OpenAI pricing request failed: %s", exc, exc_info=True)
        return fallback


def enhance_dish(
    dish: models.DishIdea,
    restaurant: models.Restaurant,
    *,
    user=None,
) -> Dict[str, Optional[str]]:
    """Generate enhanced mode assets for a dish."""
    snapshot = _format_menu_snapshot(restaurant)
    snapshot_hash = _menu_snapshot_hash(snapshot)
    price_info = _call_openai_for_price(
        dish,
        snapshot,
        menu_snapshot_hash=snapshot_hash,
        user=user,
    )

    return {
        "price_cents": price_info.get("price_cents"),
        "currency": price_info.get("currency"),
        "pricing_notes": price_info.get("rationale"),
        "style_preset": "enhanced-mode-v1",
        "model_name": "openai-enhanced",
        "snapshot": snapshot,
        "reference": str(uuid.uuid4()),
    }


def generate_dish_image_from_details(title: str, description: str, *, user=None) -> str:
    """Return a dish image generated from title and description prompts."""

    prompt = _dish_image_prompt(title, description)
    return generate_dish_image_from_prompt(
        prompt,
        DEFAULT_IMAGE_URL,
        user=user,
    )


def generate_concept_sketch(concept: models.Concept, user=None) -> str:
    """Return a concept background sketch for the provided concept."""

    prompt = _concept_sketch_prompt(concept.name, concept.subtitle)
    return generate_concept_sketch_from_prompt(
        prompt,
        DEFAULT_CONCEPT_IMAGE_URL,
        user=user,
    )
