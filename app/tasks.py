"""Celery task definitions for external service calls."""

import requests, os
from celery import shared_task
from django.conf import settings
from django.core.files.storage import default_storage
from django.utils import timezone
from app import llm, models
from dotenv import load_dotenv
load_dotenv()
import logging
logger = logging.getLogger(__name__)
from django.db import transaction
from . import models
from openai import OpenAI
_openai_api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=_openai_api_key) if _openai_api_key else None

@shared_task
def run_outscraper_search(payload_id: str) -> dict:
    """Call the Outscraper API and store the response."""
    payload = models.OutscraperPayload.objects.get(id=payload_id)
    payload.status = models.OutscraperPayload.Status.RUNNING
    payload.started_at = timezone.now()
    payload.save(update_fields=["status", "started_at"])

    headers = {"X-API-KEY": os.getenv("OUTSCRAPER_API_KEY")}
    response = requests.get(
        "https://api.app.outscraper.com/maps/search-v3",
        params=payload.request_params,
        headers=headers,
    )
    data = response.json()
    payload.response_json = data

    businesses = (
        data.get("data", [])[0]
        if data.get("data") and isinstance(data["data"][0], list)
        else []
    )
    business = businesses[0] if businesses else None

    menu_url = None

    if business:
        restaurant = payload.restaurant
        restaurant.name = business.get("name", restaurant.name)
        restaurant.location_text = business.get("full_address", restaurant.location_text)
        menu_url = business.get("menu_link") or business.get("menu_url")
        if menu_url:
            restaurant.primary_menu_url = menu_url
        restaurant.phone = business.get("phone")
        restaurant.website = business.get("site")
        restaurant.google_place_id = business.get("place_id")
        restaurant.description = business.get("description")
        restaurant.rating = business.get("rating")
        restaurant.review_count = business.get("reviews")
        restaurant.hours_json = business.get("working_hours")
        restaurant.about_json = business.get("about")
        restaurant.context_json = business
        restaurant.save()

    payload.discovered_menu_url = menu_url
    payload.status = models.OutscraperPayload.Status.SUCCEEDED
    payload.finished_at = timezone.now()
    payload.save(
        update_fields=[
            "response_json",
            "discovered_menu_url",
            "status",
            "finished_at",
        ]
    )

    if menu_url:
        mv = models.MenuVersion.objects.create(
            restaurant=payload.restaurant,
            source_url=menu_url,
            source_kind=models.MenuVersion.SourceKind.URL_SCRAPE,
            raw_markdown="",
            status=models.MenuVersion.Status.QUEUED,
        )
        scrape_menu.delay(str(mv.id))

    return data




def validate_menu_text(raw_markdown: str) -> bool:
    """Return True when the scraped markdown looks like a menu."""

    if not client or not raw_markdown or not raw_markdown.strip():
        return False

    try:
        resp = client.responses.create(
            model="gpt-4.1-mini",  # inexpensive "nano" style check
            input=(
                "You are checking if text belongs to a food or restaurant menu. "
                "Answer only 'yes' or 'no'.\n\n"
                f"Text:\n{raw_markdown[:3000]}"
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("Menu validation request failed: %s", exc, exc_info=True)
        return False

    answer = resp.output_text.strip().lower()
    return answer.startswith("y")



@shared_task(bind=True, autoretry_for=(requests.RequestException,), retry_backoff=True, max_retries=3)
def scrape_menu(self, menu_version_id: str) -> str:
    """
    Use ScraperAPI to fetch a menu in markdown format.
    Validates that the result looks like a food menu.
    Updates MenuVersion + Restaurant accordingly.
    """
    try:
        mv = models.MenuVersion.objects.get(id=menu_version_id)
    except models.MenuVersion.DoesNotExist:
        logger.error("MenuVersion %s not found, aborting scrape.", menu_version_id)
        return ""

    logger.info("Starting scrape_menu for MenuVersion %s (restaurant=%s)", mv.id, mv.restaurant.id)

    # Mark as running
    mv.status = models.MenuVersion.Status.RUNNING
    mv.error_message = ""
    mv.save(update_fields=["status", "error_message"])

    params = {
        "api_key": os.getenv("SCRAPER_API_KEY", ""),
        "url": mv.source_url,
        "render": "true",
        "output_format": "markdown",
    }

    try:
        response = requests.get("https://api.scraperapi.com/", params=params, timeout=30)
        response.raise_for_status()
        candidate_markdown = response.text or ""
        logger.debug("ScraperAPI returned %d chars for MenuVersion %s", len(candidate_markdown), mv.id)

    except Exception as exc:
        msg = f"ScraperAPI request failed: {exc}"
        logger.exception(msg)
        mv.status = models.MenuVersion.Status.FAILED
        mv.error_message = msg[:500]
        mv.save(update_fields=["status", "error_message"])
        return ""

    # Validate with LLM or simple heuristic
    is_valid_menu = False
    try:
        is_valid_menu = validate_menu_text(candidate_markdown)
        logger.info("Validation result for MenuVersion %s: %s", mv.id, is_valid_menu)
    except Exception as exc:
        logger.warning("Menu validation crashed for MenuVersion %s: %s", mv.id, exc, exc_info=True)

    if is_valid_menu:
        mv.raw_markdown = candidate_markdown
        mv.status = models.MenuVersion.Status.SUCCEEDED
        mv.parsed_at = timezone.now()
        mv.error_message = ""
    else:
        mv.raw_markdown = ""
        mv.status = models.MenuVersion.Status.FAILED
        mv.parsed_at = None
        mv.error_message = "Scraped page did not look like a menu."

    mv.save(update_fields=["raw_markdown", "status", "parsed_at", "error_message"])

    # Update restaurant’s active menu
    try:
        with transaction.atomic():
            restaurant = mv.restaurant
            restaurant.active_menu_version = mv
            if mv.source_url and not restaurant.primary_menu_url:
                restaurant.primary_menu_url = mv.source_url
            restaurant.save(update_fields=["active_menu_version", "primary_menu_url"])
        logger.info("Restaurant %s active_menu_version set to %s", restaurant.id, mv.id)
    except Exception as exc:
        logger.error("Failed to update restaurant %s with menu version %s: %s",
                     mv.restaurant.id, mv.id, exc, exc_info=True)

    return mv.raw_markdown if is_valid_menu else ""



@shared_task
def generate_concepts_task() -> list:
    """Wrapper task around the mock LLM concept generator."""
    return llm.generate_concepts()


@shared_task
def generate_dishes_task(concept: str) -> list:
    """Wrapper task around the mock LLM dish generator."""
    return llm.generate_dishes(concept)


@shared_task
def enhance_dish_task(dish_id: str) -> dict:
    """Trigger dish enhancement via background worker."""
    try:
        dish = models.DishIdea.objects.select_related("restaurant").get(id=dish_id)
    except models.DishIdea.DoesNotExist:  # pragma: no cover - defensive
        logger.warning("Dish %s not found for enhancement", dish_id)
        return {}

    return llm.enhance_dish(dish, dish.restaurant)


@shared_task
def parse_pdf_menu(menu_version_id: str, storage_path: str):
    """Send PDF to OpenAI to parse into Markdown."""
    mv = models.MenuVersion.objects.get(id=menu_version_id)
    mv.status = models.MenuVersion.Status.RUNNING
    mv.save(update_fields=["status"])

    try:
        with default_storage.open(storage_path, "rb") as f:
            files = {"file": (os.path.basename(storage_path), f, "application/pdf")}
            headers = {"Authorization": f"Bearer {settings.OPENAI_API_KEY}"}

            response = requests.post(
                "https://api.openai.com/v1/files",
                headers=headers,
                files=files,
            )
            file_id = response.json()["id"]

        payload = {
            "model": "gpt-4.1-mini",
            "input": [
                {
                    "role": "system",
                    "content": "You are an expert at extracting restaurant menus into clean Markdown.",
                },
                {
                    "role": "user",
                    "content": f"Extract the full menu in Markdown from file {file_id}.",
                },
            ],
        }
        resp = requests.post("https://api.openai.com/v1/responses", json=payload, headers=headers)
        markdown_text = resp.json().get("output_text", "")

        mv.raw_markdown = markdown_text
        mv.status = models.MenuVersion.Status.SUCCEEDED
        mv.parsed_at = timezone.now()
        mv.save(update_fields=["raw_markdown", "status", "parsed_at"])

        restaurant = mv.restaurant
        restaurant.active_menu_version = mv
        restaurant.save(update_fields=["active_menu_version"])

    except Exception as e:
        mv.status = models.MenuVersion.Status.FAILED
        mv.error_message = str(e)
        mv.save(update_fields=["status", "error_message"])

    return mv.raw_markdown
