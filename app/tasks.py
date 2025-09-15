"""Celery task definitions for external service calls."""

import requests, os
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from app import llm, models
from dotenv import load_dotenv
load_dotenv()



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

    # Outscraper usually returns a list; try to grab menu link
    businesses = data.get("data", [])
    menu_url = None
    if businesses and "menu_link" in businesses[0]:
        menu_url = businesses[0]["menu_link"]

    payload.discovered_menu_url = menu_url
    payload.status = models.OutscraperPayload.Status.SUCCEEDED
    payload.finished_at = timezone.now()
    payload.save(
        update_fields=[
            "response_json", "discovered_menu_url",
            "status", "finished_at"
        ]
    )

    # If menu_url discovered → queue scrape
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



@shared_task
def scrape_menu(menu_version_id: str) -> str:
    """Use ScraperAPI to fetch a menu in markdown format."""
    mv = models.MenuVersion.objects.get(id=menu_version_id)
    mv.status = models.MenuVersion.Status.RUNNING
    mv.save(update_fields=["status"])

    params = {
        "api_key": os.getenv('SCRAPERAPI_API_KEY'),
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
