"""Helpers for talking to LLM services.

The helpers in this module call out to third party APIs when keys are
configured and otherwise fall back to deterministic placeholder data so
tests can run without network access.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Dict, List, Optional
from openai import OpenAI

from dotenv import load_dotenv

from . import models

logger = logging.getLogger(__name__)

load_dotenv()

_openai_api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=_openai_api_key) if _openai_api_key else None
DEFAULT_IMAGE_URL = "https://placehold.co/800x600?text=Dish"
DEFAULT_CONCEPT_IMAGE_URL = "https://placehold.co/1200x800?text=Concept"
DEFAULT_PRICE_CENTS = 1500



def _concept_sketch_prompt(name: str, subtitle: str) -> str:
    """Describe a lightweight concept sketch request for OpenAI image generation."""

    subtitle_text = subtitle or ""
    return (
        "Create a monochrome pencil sketch that could serve as background art for a "
        "restaurant concept card. Keep the lines clean with minimal shading so the image "
        "stays lightweight, but output it at a high-definition resolution. Avoid text, "
        "logos, or color.  Should only be of a singular item, not a spread or motif.\n"
        f"Concept name: {name}\n"
        f"Concept subtitle: {subtitle_text}"
    )

def _dish_image_prompt(title: str, description: str) -> str:
    """Return the prompt text for generating a dish image."""

    description_text = description or ""
    return (
        "Create a realistic professional photograph of the following dish on a "
        "transparent background, as if the dish is hovering on a white plane.\n"
        f"Title: {title}\n"
        f"Description: {description_text}"
    )

def _fetch_openai_image(prompt: str, default_url: str) -> str:
    """Return inline image data (base64) for the given prompt or a fallback URL."""

    if not client:
        return default_url

    try:
        response = client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            quality="low",
            background="transparent",
            n=1,
            response_format="b64_json",
            size="1024x1024",
            output_format="png",
        )

        if response.data and "b64_json" in response.data[0]:
            base64_data = response.data[0]["b64_json"]
            return f"data:image/png;base64,{base64_data}"

        logger.warning("OpenAI image response did not include b64_json data.")

    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("OpenAI image generation failed: %s", exc, exc_info=True)

    return default_url


def _call_openai_for_image(title: str, description: str) -> str:
    """Return a data URL for the generated dish image using the OpenAI Images API."""

    prompt = _dish_image_prompt(title, description)
    return _fetch_openai_image(prompt, DEFAULT_IMAGE_URL)


def _call_openai_for_concept_sketch(name: str, subtitle: str) -> str:
    """Return an OpenAI generated concept sketch or a placeholder image."""

    prompt = _concept_sketch_prompt(name, subtitle)
    return _fetch_openai_image(prompt, DEFAULT_CONCEPT_IMAGE_URL)



def _format_menu_snapshot(restaurant: models.Restaurant) -> Dict[str, Optional[str]]:
    menu_markdown = ""
    if restaurant.active_menu_version and restaurant.active_menu_version.raw_markdown:
        menu_markdown = restaurant.active_menu_version.raw_markdown
    return {
        "restaurant": restaurant.context_json or {},
        "menu_markdown": menu_markdown,
    }


def _call_openai_for_price(
    dish: models.DishIdea, menu_snapshot: Dict[str, Optional[str]]
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
            model="gpt-4.1-mini",
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
        return {
            "price_cents": price_cents,
            "currency": currency,
            "rationale": rationale,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("OpenAI pricing request failed: %s", exc, exc_info=True)
        return fallback


def enhance_dish(dish: models.DishIdea, restaurant: models.Restaurant) -> Dict[str, Optional[str]]:
    """Generate enhanced mode assets for a dish."""

    image_url = _call_openai_for_image(dish.title, dish.description)
    snapshot = _format_menu_snapshot(restaurant)
    price_info = _call_openai_for_price(dish, snapshot)

    return {
        "image_url": image_url,
        "price_cents": price_info.get("price_cents"),
        "currency": price_info.get("currency"),
        "pricing_notes": price_info.get("rationale"),
        "style_preset": "enhanced-mode-v1",
        "model_name": "openai-enhanced",
        "snapshot": snapshot,
        "reference": str(uuid.uuid4()),
    }


def generate_concept_sketch(concept: models.Concept) -> str:
    """Return a concept background sketch for the provided concept."""

    return _call_openai_for_concept_sketch(concept.name, concept.subtitle)
