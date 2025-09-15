"""Celery task definitions for external service calls."""

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from app import llm, models


@shared_task
def run_outscraper_search(payload_id: str) -> dict:
    """Call the Outscraper API and store the response."""
    payload = models.OutscraperPayload.objects.get(id=payload_id)
    payload.status = models.OutscraperPayload.Status.RUNNING
    payload.started_at = timezone.now()
    payload.save(update_fields=["status", "started_at"])

    headers = {"X-API-KEY": getattr(settings, "OUTSCRAPER_API_KEY", "")}
    response = requests.get(
        "https://api.app.outscraper.com/maps/search-v3",
        params=payload.request_params,
        headers=headers,
    )
    payload.response_json = response.json()
    payload.discovered_menu_url = payload.response_json.get("menu_link")
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
    return payload.response_json


@shared_task
def scrape_menu(menu_version_id: str) -> str:
    """Use ScraperAPI to fetch a menu in markdown format."""
    mv = models.MenuVersion.objects.get(id=menu_version_id)
    mv.status = models.MenuVersion.Status.RUNNING
    mv.save(update_fields=["status"])

    params = {
        "api_key": getattr(settings, "SCRAPERAPI_API_KEY", ""),
        "url": mv.source_url,
        "render": "true",
        "output_format": "markdown",
    }
    response = requests.get("https://api.scraperapi.com/", params=params)
    mv.raw_markdown = response.text
    mv.status = models.MenuVersion.Status.SUCCEEDED
    mv.parsed_at = timezone.now()
    mv.save(update_fields=["raw_markdown", "status", "parsed_at"])
    return mv.raw_markdown


@shared_task
def generate_concepts_task() -> list:
    """Wrapper task around the mock LLM concept generator."""
    return llm.generate_concepts()


@shared_task
def generate_dishes_task(concept: str) -> list:
    """Wrapper task around the mock LLM dish generator."""
    return llm.generate_dishes(concept)


@shared_task
def enhance_dish_task(title: str) -> dict:
    """Wrapper task around dish enhancement."""
    return llm.enhance_dish(title)
