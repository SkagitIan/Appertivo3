"""Celery tasks for the leads app."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Iterable, List, Sequence

import requests
from celery import chain, shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency for local dev
    OpenAI = None  # type: ignore

from .models import Concept, DishIdea, Lead
from .utils import pick_city

logger = logging.getLogger(__name__)


@dataclass
class GeneratedConcept:
    """Simple structure for concept data returned from OpenAI."""

    name: str
    enhanced: bool = False


@dataclass
class GeneratedDish:
    """Simple structure for dish data returned from OpenAI."""

    title: str
    favorited: bool = False
    concept_index: int | None = None
    image_url: str | None = None


def _get_openai_client():
    """Return an OpenAI client if the dependency is installed."""

    if OpenAI is None:
        raise RuntimeError("openai package is not installed")
    api_key = getattr(settings, "OPENAI_API_KEY", None)
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    return OpenAI(api_key=api_key)


@shared_task(bind=True)
def fetch_leads(self) -> List[int]:
    """Fetch leads from the Outscraper API and create Lead entries."""

    city = pick_city()
    logger.info("Fetching leads for city %s", city)
    api_key = getattr(settings, "OUTSCRAPER_API_KEY", None)
    if not api_key:
        logger.warning("OUTSCRAPER_API_KEY not configured; skipping fetch")
        return []

    params = {
        "query": f"independent restaurants in {city}",
        "limit": 10,
    }
    headers = {"X-API-KEY": api_key}
    try:
        response = requests.get(
            "https://api.app.outscraper.com/maps/search-business", params=params, headers=headers, timeout=60
        )
        response.raise_for_status()
    except requests.RequestException as exc:  # pragma: no cover - network failure
        logger.exception("Outscraper request failed: %s", exc)
        return []

    payload = response.json()
    if isinstance(payload, dict) and "data" in payload:
        results: Sequence[dict] = payload.get("data", [])
    elif isinstance(payload, list):
        results = payload
    else:
        logger.warning("Unexpected Outscraper payload: %s", payload)
        return []

    created_ids: List[int] = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        lead_defaults = {
            "name": entry.get("name") or entry.get("title") or "Unknown Restaurant",
            "email": entry.get("email"),
            "phone": entry.get("phone"),
            "city": entry.get("city") or city,
            "json_data": entry,
        }
        identifier = entry.get("email") or entry.get("phone") or entry.get("name")
        if not identifier:
            continue
        email = entry.get("email")
        if email:
            lead, created = Lead.objects.update_or_create(
                email=email,
                defaults=lead_defaults,
            )
        else:
            lead = Lead.objects.create(**lead_defaults)
            created = True
        if created:
            created_ids.append(lead.id)
        else:
            # When updating an existing lead ensure we keep slug and city aligned.
            for field, value in lead_defaults.items():
                setattr(lead, field, value)
            lead.save()
            created_ids.append(lead.id)
    logger.info("Prepared %s leads for generation", len(created_ids))
    return created_ids


@shared_task(bind=True)
def generate_concepts_and_dishes(self, lead_id: int) -> int:
    """Generate concepts and dishes for a lead using OpenAI."""

    lead = Lead.objects.get(pk=lead_id)
    try:
        client = _get_openai_client()
    except RuntimeError as exc:
        logger.warning("Skipping OpenAI generation for lead %s: %s", lead_id, exc)
        return lead_id

    prompt = """
    Generate three distinct culinary pop-up concepts and six dish ideas for an independent restaurant.
    Return JSON with keys 'concepts' and 'dishes'. Concepts should include name and enhanced flag.
    Dishes should include title, favorited (boolean) and concept_index referencing the concept order.
    """.strip()
    response = client.responses.create(
        model=getattr(settings, "OPENAI_LEADS_MODEL", "gpt-4.1-mini"),
        input=[{"role": "system", "content": "You are a culinary creative director."}, {"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    try:
        content = response.output[0].content[0].text  # type: ignore[attr-defined]
        data = json.loads(content)
    except Exception as exc:  # pragma: no cover - depends on API response
        logger.exception("Failed parsing OpenAI response for lead %s: %s", lead_id, exc)
        return lead_id

    concepts_data = [
        GeneratedConcept(name=item.get("name", f"Concept {index + 1}"), enhanced=bool(item.get("enhanced")))
        for index, item in enumerate(data.get("concepts", [])[:3])
    ]
    dishes_data = []
    for item in data.get("dishes", [])[:6]:
        dishes_data.append(
            GeneratedDish(
                title=item.get("title", "Signature Dish"),
                favorited=bool(item.get("favorited")),
                concept_index=item.get("concept_index"),
                image_url=item.get("image_url"),
            )
        )

    lead.concepts.all().delete()
    lead.dishes.all().delete()
    created_concepts: List[Concept] = []
    for idx, concept in enumerate(concepts_data, start=1):
        created_concepts.append(
            Concept.objects.create(
                lead=lead,
                name=concept.name,
                rank_order=idx,
                enhanced=concept.enhanced if idx == 1 else concept.enhanced,
            )
        )
    if created_concepts and created_concepts[0].enhanced is False:
        created_concepts[0].enhanced = True
        created_concepts[0].save(update_fields=["enhanced"])

    for index, dish in enumerate(dishes_data):
        concept = None
        if dish.concept_index is not None and 0 <= dish.concept_index < len(created_concepts):
            concept = created_concepts[dish.concept_index]
        DishIdea.objects.create(
            lead=lead,
            concept=concept,
            title=dish.title,
            favorited=dish.favorited if index < 3 else dish.favorited,
            image_url=dish.image_url,
        )
    return lead_id


@shared_task(bind=True)
def send_personalized_email(self, lead_id: int) -> int:
    """Send a personalized outreach email to a lead."""

    lead = Lead.objects.get(pk=lead_id)
    if not lead.email:
        logger.info("Lead %s has no email; skipping outreach", lead_id)
        return lead_id

    context = {"lead": lead}
    subject = f"{lead.name}, explore your Appertivo tasting demo"
    text_body = render_to_string("leads/emails/outreach.txt", context)
    html_body = render_to_string("leads/emails/outreach.html", context)

    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [lead.email])
    message.attach_alternative(html_body, "text/html")
    message.send(fail_silently=False)

    lead.emailed = True
    lead.save(update_fields=["emailed"])
    return lead_id


@shared_task(bind=True)
def dispatch_lead_pipeline(self, lead_ids: Iterable[int]) -> None:
    """Kick off concept generation and outreach for each fetched lead."""

    for lead_id in lead_ids:
        chain(generate_concepts_and_dishes.s(lead_id), send_personalized_email.s()).delay()


def build_lead_pipeline() -> None:
    """Trigger the full fetch → generate → email pipeline."""

    fetch_leads.apply_async(link=dispatch_lead_pipeline.s())
