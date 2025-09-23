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
import cloudinary
import base64

from dotenv import load_dotenv

from . import models

logger = logging.getLogger(__name__)

load_dotenv()

_openai_api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=_openai_api_key) if _openai_api_key else None
DEFAULT_IMAGE_URL = "https://placehold.co/800x600?text=Dish"
DEFAULT_CONCEPT_IMAGE_URL = "https://placehold.co/1200x800?text=Concept"
DEFAULT_PRICE_CENTS = 1500

cloudinary.config( 
  cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME"), 
  api_key = os.getenv("CLOUDINARY_API_KEY"), 
  api_secret = os.getenv("CLOUDINARY_SECRET_KEY")
)

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
    prompt = f"""
    "Professional food photography of {title}. {description_text}. Shot with a DSLR camera using a 35mm lens 
    at f/4 aperture, positioned as if seated at the table looking down at the meal. The dish is freshly 
    plated and just set down on a complete table setting - white linen tablecloth or clean wooden 
    table, with silverware properly placed, cloth napkin, and a water glass or appropriate beverage 
    visible in frame. Soft overhead restaurant lighting with warm ambient tones, creating natural shadows. 
    The plate appears to have just arrived - garnishes are fresh, sauces haven't been touched, and any hot 
    dishes show gentle steam rising. Include subtle details like breadcrumbs on the table or a partially 
    visible menu edge to enhance the dining experience feel. Colors are natural and inviting. Sharp focus 
    on the main dish with the table setting softly detailed. The composition feels intimate and welcoming, 
    like you're about to take your first bite. Restaurant photography style with a lived-in, authentic 
    dining atmosphere."
    """


    return prompt


def _fetch_openai_sketch(prompt: str, default_url: str) -> str:
    """Generate an image via OpenAI, upload to Cloudinary, return optimized URL."""
    if not client:
        return default_url

    try:
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            quality="standard",
            style="vivid",
            #n=1,
            size="1024x1024",
            #output_format="png",
            response_format='b64_json'
        )
        if response.data and getattr(response.data[0], "b64_json", None):
            base64_data = response.data[0].b64_json
            image_bytes = base64.b64decode(base64_data)

            # Upload to Cloudinary
            upload_result = cloudinary.uploader.upload(
                image_bytes,
                folder="appertivo/dishes",
                public_id=str(uuid.uuid4()),
                overwrite=True,
                resource_type="image",
            )

            # Cloudinary can deliver resized/optimized variants with URL params
            optimized_url = cloudinary.CloudinaryImage(upload_result["public_id"]).build_url(
                width=500,
                height=500,
                crop="fill",
                quality="auto",
                fetch_format="auto",
            )
            return optimized_url

        logger.warning("OpenAI image response did not include b64_json data.")

    except Exception as exc:
        logger.warning("OpenAI image generation failed: %s", exc, exc_info=True)

    return default_url


def _fetch_openai_image(prompt: str, default_url: str) -> str:
    """Generate an image via OpenAI, upload to Cloudinary, return optimized URL."""
    if not client:
        return default_url

    try:
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            quality="hd",
            style="natural",
            #n=1,
            size="1024x1024",
            #output_format="png",
            response_format='b64_json'
        )
        if response.data and getattr(response.data[0], "b64_json", None):
            base64_data = response.data[0].b64_json
            image_bytes = base64.b64decode(base64_data)

            # Upload to Cloudinary
            upload_result = cloudinary.uploader.upload(
                image_bytes,
                folder="appertivo/dishes",
                public_id=str(uuid.uuid4()),
                overwrite=True,
                resource_type="image",
            )

            # Cloudinary can deliver resized/optimized variants with URL params
            optimized_url = cloudinary.CloudinaryImage(upload_result["public_id"]).build_url(
                width=500,
                height=500,
                crop="fill",
                quality="auto",
                fetch_format="auto",
            )
            return optimized_url

        logger.warning("OpenAI image response did not include b64_json data.")

    except Exception as exc:
        logger.warning("OpenAI image generation failed: %s", exc, exc_info=True)

    return default_url


def _call_openai_for_image(title: str, description: str) -> str:
    """Return a data URL for the generated dish image using the OpenAI Images API."""

    prompt = _dish_image_prompt(title, description)
    return _fetch_openai_image(prompt, DEFAULT_IMAGE_URL)


def _call_openai_for_concept_sketch(name: str, subtitle: str) -> str:
    """Return an OpenAI generated concept sketch or a placeholder image."""

    prompt = _concept_sketch_prompt(name, subtitle)
    return _fetch_openai_sketch(prompt, DEFAULT_CONCEPT_IMAGE_URL)



def _format_menu_snapshot(restaurant: models.Restaurant) -> Dict[str, Optional[str]]:
    menu_markdown = ""
    if restaurant.active_menu_version and restaurant.active_menu_version.raw_markdown:
        menu_markdown = restaurant.active_menu_version.raw_markdown
    return {
        "restaurant": restaurant.context_json or {},
        "menu_markdown": menu_markdown,
    }


def _call_openai_for_price(dish: models.DishIdea, menu_snapshot: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
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
    snapshot = _format_menu_snapshot(restaurant)
    price_info = _call_openai_for_price(dish, snapshot)

    return {
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
