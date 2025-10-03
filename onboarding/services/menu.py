"""Services for capturing a menu snapshot during onboarding."""

from __future__ import annotations

import logging
import re
import time
from decimal import Decimal
from typing import Iterable, List

import requests
from django.db import transaction

from app import models

logger = logging.getLogger(__name__)
_TIMEOUT_SECONDS = 12
_MAX_MENU_PAGES = 3


def _log_snapshot(metadata: dict) -> None:
    try:
        models.LlmCallLog.objects.create(
            provider="menu_snapshot",
            model_name="crawler",
            call_type=models.LlmCallLog.CallType.TEXT,
            step="onboarding",
            function_name="snapshot_and_normalize",
            metadata=metadata,
        )
    except Exception:  # pragma: no cover - best effort logging
        logger.exception("Failed to record menu snapshot metadata", exc_info=True)


def _menu_candidates(onboarding: models.Onboarding) -> List[str]:
    restaurant = onboarding.restaurant
    urls: List[str] = []
    if restaurant and restaurant.menu_urls:
        urls.extend([str(url) for url in restaurant.menu_urls if url])
    profile_links = onboarding.web_profile_json or {}
    if isinstance(profile_links, dict):
        urls.extend(profile_links.get("menu_links", []))
    cleaned: List[str] = []
    for url in urls:
        normalized = url.strip()
        if not normalized:
            continue
        if normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned[:_MAX_MENU_PAGES]


def _collect_menu_text(urls: Iterable[str]) -> str:
    chunks: List[str] = []
    for url in urls:
        try:
            started = time.monotonic()
            response = requests.get(url, timeout=_TIMEOUT_SECONDS)
            response.raise_for_status()
            latency_ms = int((time.monotonic() - started) * 1000)
        except requests.RequestException as exc:
            logger.warning("Unable to fetch menu url %s: %s", url, exc)
            continue
        chunks.append(f"# Source: {url}\n\n{response.text}\n")
        _log_snapshot({"url": url, "latency_ms": latency_ms})
    return "\n".join(chunks)


def _parse_price(text: str) -> tuple[Decimal | None, str | None]:
    match = re.search(r"(?P<currency>[$€£])\s?(?P<value>\d+(?:\.\d{1,2})?)", text)
    if not match:
        return None, None
    value = Decimal(match.group("value"))
    currency = match.group("currency")
    return value, currency


def _extract_items(raw_text: str) -> List[dict]:
    items: List[dict] = []
    for line in raw_text.splitlines():
        cleaned = line.strip()
        if not cleaned or len(cleaned.split()) < 2:
            continue
        price, currency = _parse_price(cleaned)
        item_name = cleaned
        if price is not None:
            item_name = cleaned.split(str(price))[0].strip()
        if item_name:
            items.append(
                {
                    "name": item_name,
                    "price_cents": int(price * 100) if price is not None else None,
                    "currency": currency,
                }
            )
    return items


def _ingredients_from_items(items: Iterable[dict]) -> List[str]:
    ingredients: set[str] = set()
    for item in items:
        name = str(item.get("name", ""))
        for chunk in re.split(r"[,/\-]", name):
            token = chunk.strip().lower()
            if token and 2 <= len(token) <= 40:
                ingredients.add(token)
    return sorted(ingredients)


def snapshot_and_normalize(onboarding: models.Onboarding) -> models.MenuVersion:
    """Fetch menu pages and persist a MenuVersion with extracted ingredients."""

    restaurant = onboarding.restaurant
    if not restaurant:
        raise ValueError("Onboarding requires an associated restaurant")

    urls = _menu_candidates(onboarding)
    if not urls:
        with transaction.atomic():
            version = models.MenuVersion.objects.create(
                restaurant=restaurant,
                source_url="",
                source_kind=models.MenuVersion.SourceKind.PASTED_TEXT,
                raw_markdown="",
                status=models.MenuVersion.Status.SUCCEEDED,
            )
            restaurant.active_menu_version = version
            restaurant.save(update_fields=["active_menu_version"])
        return version

    raw_text = _collect_menu_text(urls)
    if not raw_text:
        raw_text = "\n".join(f"# Source: {url}" for url in urls)

    items = _extract_items(raw_text)
    ingredient_names = _ingredients_from_items(items)

    with transaction.atomic():
        version = models.MenuVersion.objects.create(
            restaurant=restaurant,
            source_url=urls[0],
            source_kind=models.MenuVersion.SourceKind.URL_SCRAPE,
            raw_markdown=raw_text,
            status=models.MenuVersion.Status.SUCCEEDED,
        )
        restaurant.active_menu_version = version
        restaurant.set_menu_urls(urls)
        restaurant.save(update_fields=["active_menu_version", "menu_urls", "primary_menu_url"])

        existing = set(
            models.Ingredient.objects.filter(restaurant=restaurant, name__in=ingredient_names).values_list("name", flat=True)
        )
        to_create = [
            models.Ingredient(
                restaurant=restaurant,
                name=name,
                canonical_name=name,
                first_seen_menu_version=version,
            )
            for name in ingredient_names
            if name not in existing
        ]
        if to_create:
            models.Ingredient.objects.bulk_create(to_create, ignore_conflicts=True)

    return version
