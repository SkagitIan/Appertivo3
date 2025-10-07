"""Utilities for building a lightweight web profile snapshot."""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Iterable, List
from urllib.parse import urljoin, urlparse

import requests

from app import models

logger = logging.getLogger(__name__)
_MAX_PAGES = 6
_TIMEOUT_SECONDS = 10


@dataclass
class PageSnapshot:
    """Lightweight snapshot of a crawled page."""

    url: str
    text: str
    links: List[str]


class _SimpleHTMLParser(HTMLParser):
    """Collect text and anchor links from HTML content."""

    def __init__(self) -> None:
        super().__init__()
        self.text_parts: List[str] = []
        self.links: List[str] = []

    def handle_data(self, data: str) -> None:  # pragma: no cover - html parsing is straightforward
        cleaned = (data or "").strip()
        if cleaned:
            self.text_parts.append(cleaned)

    def handle_starttag(self, tag: str, attrs):  # pragma: no cover - html parsing is straightforward
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.links.append(value)


def _log_profile_call(metadata: dict) -> None:
    """Record a profile crawl for observability."""

    try:
        models.LlmCallLog.objects.create(
            provider="web_profile",
            model_name="crawler",
            call_type=models.LlmCallLog.CallType.TEXT,
            step="onboarding",
            function_name="build_profile",
            metadata=metadata,
        )
    except Exception:  # pragma: no cover - best effort logging
        logger.exception("Failed to record web profile crawl", exc_info=True)


def _normalize_domain(domain: str) -> str:
    parsed = urlparse(domain)
    if parsed.scheme:
        return parsed.netloc.lower()
    return domain.lower().lstrip("www.")


def _should_visit(url: str, allowed_netlocs: set[str]) -> bool:
    parsed = urlparse(url)
    if not parsed.netloc:
        return True
    netloc = parsed.netloc.lower().lstrip("www.")
    return netloc in allowed_netlocs


def _parse_html(url: str, html: str) -> PageSnapshot:
    parser = _SimpleHTMLParser()
    parser.feed(html)
    return PageSnapshot(url=url, text=" ".join(parser.text_parts), links=parser.links)


def _extract_menu_links(links: Iterable[str]) -> List[str]:
    results: List[str] = []
    for link in links:
        if "menu" in link.lower() and link not in results:
            results.append(link)
    return results


def _extract_contact(text: str) -> dict:
    phones = set(re.findall(r"\\+?\d[\d\-\s]{7,}\d", text))
    emails = set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text))
    return {
        "phones": sorted(phones),
        "emails": sorted(emails),
    }

def web_search_profile_prompt():
    prompt = f"""
        You are a precise restaurant analyst. Use the web_search tool to thoroughly explore the restaurant’s site and any directly linked pages/PDFs within allowed_domains only.

        GOALS
        1) Atmosphere & Identity
        • Describe the restaurant’s style, aesthetic, ambiance, and brand personality (concise, vivid).

        2) Menu Links & Structure
        • Collect ALL menu URLs (HTML, PDFs, embeds).
        • For each menu section, list items with: name, description, price_cents (integer or null), currency (ISO code or null), allergens (array).
        • Provide a section-level source_url (page or PDF URL where the section was found).

        3) Contact & Operational
        • phone, email, address (strings).
        • reservation_url (string; empty string if not present).
        • social_links (array of absolute URLs; empty if none).

        4) Personas (EXACTLY THREE)
        • Return an array of exactly 3 paragraphs (strings).
        • Each paragraph is 2–4 sentences describing a distinct guest persona grounded in site evidence (and reviews if linked).

        5) Master Ingredient List
        • Parse all menu item names and descriptions to extract ingredients.
        • Normalize to singular, lowercase US spelling.
        • Return ONLY a de-duplicated array of ingredient names (strings).

        RULES
        • Stay within allowed_domains = Absolute URLs only.
        • If a field is unknown, still include it with the correct empty value type.
        • Return ONLY valid JSON conforming exactly to the schema named “restaurant_profile”.
        """
    return prompt

def web_search_profile_schema():
    schema = {
        "name": "restaurant_profile",
        "type": "json_schema",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "style_vibe": {"type": "string"},
                "menu_urls": {"type": "array", "items": {"type": "string"}},
                "menus": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "section": {"type": "string"},
                            "source_url": {"type": "string"},
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "name": {"type": "string"},
                                        "description": {"type": "string"},
                                        "price_cents": {"type": ["integer", "null"]},
                                        "currency": {"type": ["string", "null"]},
                                        "allergens": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                    },
                                    "required": [
                                        "name",
                                        "description",
                                        "price_cents",
                                        "currency",
                                        "allergens",
                                    ],
                                },
                            },
                        },
                        "required": ["section", "source_url", "items"],
                    },
                },
                "contact": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "phone": {"type": "string"},
                        "email": {"type": "string"},
                        "address": {"type": "string"},
                        "reservation_url": {"type": "string"},
                        "social_links": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "phone",
                        "email",
                        "address",
                        "reservation_url",
                        "social_links",
                    ],
                },
                "personas": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "string"},
                },
                "ingredients": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "style_vibe",
                "menu_urls",
                "menus",
                "contact",
                "personas",
                "ingredients",
            ],
        },
    }
    return schema

def build_profile(onboarding: models.Onboarding, website) -> dict:
    raw_url = (website or "").strip()
    if not raw_url:
        return {"domain": "", "menu_links": [], "contact": {}, "about": ""}

    # normalize to domain.com form
    parsed = urlparse(raw_url if "://" in raw_url else f"https://{raw_url}")
    domain = parsed.netloc or parsed.path
    domain = domain.lower().removeprefix("www.")
    # --- Execute web search via OpenAI ---
    logger.info(domain)
    try:
        response = client.responses.create(
            model="gpt-5",
            tools=[
                {
                    "type": "web_search",
                    "filters": {"allowed_domains": allowed_domains},
                }
            ],
            input=web_search_profile_prompt(),
            text={"format": web_search_profile_schema()},
        )

        profile = response.output_parsed or {}
        logger.info(profile)
        if not profile:
            logger.error("Empty response for restaurant %s", restaurant.id)
            return None

        # --- Save structured data to DB ---
        restaurant.websearch_json = json.dumps(profile, indent=2)
        restaurant.menu_json = json.dumps(profile.get("menus", []))
        restaurant.ingredients_json = json.dumps(profile.get("ingredients", []))
        restaurant.save()

        logger.info("Saved web profile for %s", restaurant.name)
        return profile

    except Exception as e:
        logger.exception("Error building profile for %s: %s", restaurant.id, e)
        return None