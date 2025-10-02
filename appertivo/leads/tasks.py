"""Celery tasks for the leads app."""
from __future__ import annotations
import os
from dotenv import load_dotenv
load_dotenv()
import json
import logging
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Sequence

import requests
from outscraper import ApiClient
from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import F
from django.template.loader import render_to_string
from django.urls import reverse

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency for local dev
    OpenAI = None  # type: ignore

from .models import Concept, DishIdea, EmailTemplate, Lead, LeadRun
from .utils import extract_outscraper_job_id, pick_city


DEFAULT_WEBHOOK_URL = "https://appertivo.com/leads/outscraper-webhook/"


def get_outscraper_webhook_url() -> str:
    """Return the configured Outscraper webhook URL for lead imports."""

    return "https://appertivo.com/leads/outscraper-webhook/"


def _create_outscraper_client(api_key: str | None) -> ApiClient | None:
    """Instantiate the Outscraper SDK client when credentials are available."""

    if not api_key:
        return None
    try:
        return ApiClient(api_key=api_key)
    except Exception as exc:  # pragma: no cover - depends on SDK internals
        logger.exception("Failed to create Outscraper client: %s", exc)
        return None


def extract_lead_entries(payload: object) -> list[dict]:
    """Return a flat list of lead dictionaries from an Outscraper payload."""

    if isinstance(payload, dict):
        candidates = (
            payload.get("data")
            or payload.get("Data")
            or payload.get("results")
            or payload.get("Results")
        )
        if isinstance(candidates, list):
            if candidates and isinstance(candidates[0], list):
                return [entry for entry in candidates[0] if isinstance(entry, dict)]
            return [entry for entry in candidates if isinstance(entry, dict)]
        if isinstance(candidates, dict):
            nested = candidates.get("data") or candidates.get("results")
            if isinstance(nested, list):
                return [entry for entry in nested if isinstance(entry, dict)]
    elif isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    return []


def resolve_outscraper_payload(payload: object, headers: Mapping[str, str] | None = None) -> object:
    """Fetch Outscraper job results when the initial payload only includes metadata."""

    if not isinstance(payload, dict):
        return payload

    if extract_lead_entries(payload):
        return payload

    results_url = payload.get("results_location")
    request_headers = dict(headers or {})
    if results_url:
        try:
            response = requests.get(results_url, headers=request_headers or None, timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:  # pragma: no cover - network failure
            logger.exception("Failed to download Outscraper results: %s", exc)

    job_id = str(payload.get("id") or payload.get("job_id") or payload.get("task_id") or "").strip()
    if not job_id:
        return payload

    api_key = None
    if request_headers:
        api_key = request_headers.get("X-API-KEY")
    api_key = api_key or os.getenv("OUTSCRAPER_API_KEY") or getattr(settings, "OUTSCRAPER_API_KEY", None)

    client = _create_outscraper_client(api_key)
    if client is not None:
        try:
            job_payload = client.get_request_archive(job_id)
            if (
                isinstance(job_payload, dict)
                and "Data" in job_payload
                and "data" not in job_payload
            ):
                job_payload["data"] = job_payload["Data"]
            return job_payload
        except Exception as exc:  # pragma: no cover - network failure
            logger.exception("Failed to fetch Outscraper job %s via SDK: %s", job_id, exc)

    job_url = f"https://api.outscraper.cloud/requests/{job_id}"
    try:
        response = requests.get(job_url, headers=request_headers or None, timeout=60)
        response.raise_for_status()
        job_payload = response.json()
        if (
            isinstance(job_payload, dict)
            and "Data" in job_payload
            and "data" not in job_payload
        ):
            job_payload["data"] = job_payload["Data"]
        return job_payload
    except requests.RequestException as exc:  # pragma: no cover - network failure
        logger.exception("Failed to fetch Outscraper job %s: %s", job_id, exc)
        return payload


def store_lead_entries(
    entries: Sequence[Mapping[str, object]],
    *,
    city: str | None = None,
    run: LeadRun | None = None,
) -> list[int]:
    """Create or update Lead objects for the supplied Outscraper entries."""

    created_ids: list[int] = []

    def _clean_list(values: Iterable[object | None]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            if isinstance(value, str):
                candidate = value.strip()
                if candidate and candidate not in cleaned:
                    cleaned.append(candidate)
        return cleaned

    def _coerce_float(value: object | None) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    for entry in entries:
        if not isinstance(entry, Mapping):
            continue

        email_candidates = [
            entry.get("email"),
            entry.get("email_1"),
            entry.get("email_2"),
            entry.get("email_3"),
        ]
        emails = _clean_list(email_candidates)
        extra_emails = entry.get("emails")
        if isinstance(extra_emails, Iterable) and not isinstance(extra_emails, (str, bytes)):
            emails.extend(_clean_list(extra_emails))
            emails = _clean_list(emails)

        phone_candidates = [
            entry.get("phone"),
            entry.get("phone_1"),
            entry.get("phone_2"),
            entry.get("phone_3"),
        ]
        phones = _clean_list(phone_candidates)
        extra_phones = entry.get("phones")
        if isinstance(extra_phones, Iterable) and not isinstance(extra_phones, (str, bytes)):
            phones.extend(_clean_list(extra_phones))
            phones = _clean_list(phones)

        socials: dict[str, str] = {}
        for key in [
            "facebook",
            "instagram",
            "linkedin",
            "tiktok",
            "twitter",
            "youtube",
            "vimeo",
            "github",
            "twitch",
            "telegram",
            "whatsapp",
            "medium",
            "snapchat",
            "reddit",
        ]:
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                socials[key] = value.strip()

        menu_links = _clean_list([entry.get("menu_link")])
        entry_menu_links = entry.get("menu_links")
        if isinstance(entry_menu_links, Iterable) and not isinstance(entry_menu_links, (str, bytes)):
            menu_links.extend(_clean_list(entry_menu_links))
            menu_links = _clean_list(menu_links)

        order_links: list[str] = []
        entry_order_links = entry.get("order_links")
        if isinstance(entry_order_links, Iterable) and not isinstance(entry_order_links, (str, bytes)):
            order_links.extend(_clean_list(entry_order_links))
        elif isinstance(entry.get("order_link"), str):
            order_links.append(str(entry.get("order_link")))
        order_links = _clean_list(order_links)

        working_hours = entry.get("working_hours")
        if not isinstance(working_hours, Mapping):
            working_hours = {}

        lead_defaults: dict[str, object | None] = {
            "name": entry.get("name") or entry.get("title") or "Unknown Restaurant",
            "email": emails[0] if emails else entry.get("email"),
            "phone": phones[0] if phones else entry.get("phone"),
            "city": entry.get("city") or city,
            "full_address": entry.get("full_address") or entry.get("address"),
            "latitude": _coerce_float(entry.get("latitude")),
            "longitude": _coerce_float(entry.get("longitude")),
            "website": entry.get("site") or entry.get("website"),
            "google_place_id": entry.get("place_id") or entry.get("google_id"),
            "description": entry.get("description"),
            "rating": entry.get("rating"),
            "review_count": entry.get("reviews_count") or entry.get("reviews"),
            "price_level": entry.get("range") or entry.get("price_level"),
            "emails": emails,
            "phones": phones,
            "social_links": socials,
            "menu_links": menu_links,
            "order_links": order_links,
            "hours": working_hours,
            "json_data": dict(entry),
        }
        if run is not None:
            lead_defaults["run"] = run
            lead_defaults["shortlisted"] = False
        identifier = (
            lead_defaults["email"]
            or lead_defaults["phone"]
            or entry.get("name")
            or entry.get("place_id")
        )
        if not identifier:
            continue
        email = lead_defaults.get("email")
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
            for field, value in lead_defaults.items():
                setattr(lead, field, value)
            lead.save()
            created_ids.append(lead.id)
    return created_ids

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
def fetch_leads(
    self,
    run_id: int | None = None,
    city: str | None = None,
    limit: int = 10,
) -> List[int]:
    """Fetch leads from the Outscraper API and create Lead entries."""

    run: LeadRun | None = None
    if run_id is not None:
        try:
            run = LeadRun.objects.get(pk=run_id)
        except LeadRun.DoesNotExist:
            run = None
        else:
            status_updates = {"status": LeadRun.Status.FETCHING}
            if city:
                status_updates["city"] = city
            if limit:
                status_updates["expected_leads"] = max(1, limit)
            for field, value in status_updates.items():
                setattr(run, field, value)
            run.save(update_fields=list(status_updates.keys()))

    if not city:
        if run and run.city:
            city = run.city
        else:
            city = pick_city()
    logger.info("Fetching leads for city %s", city)
    api_key = os.getenv("OUTSCRAPER_API_KEY") or getattr(settings, "OUTSCRAPER_API_KEY", None)
    client = _create_outscraper_client(api_key)
    if client is None:
        logger.warning("OUTSCRAPER_API_KEY not configured; skipping fetch")
        return []

    query = f"independent restaurants in {city}"
    try:
        payload = client.google_maps_search(
            query,
            limit=max(1, limit),
            enrichment=["domains_service"],
            fields=[
                "query",
                "name",
                "place_id",
                "full_address",
                "latitude",
                "longitude",
                "site",
                "phone",
                "type",
                "description",
                "category",
                "subtypes",
                "about",
                "menu_link",
                "menu_links",
                "order_links",
                "working_hours",
                "email",
                "email_1",
                "email_2",
                "email_3",
                "phone_1",
                "phone_2",
                "phone_3",
                "instagram",
                "facebook",
                "twitter",
                "tiktok",
                "linkedin",
                "youtube",
            ],
        )
    except Exception as exc:  # pragma: no cover - network failure/SDK error
        logger.exception("Outscraper request failed: %s", exc)
        return []

    job_id = extract_outscraper_job_id(payload)
    if run is not None and job_id and run.outscraper_job_id != job_id:
        run.outscraper_job_id = job_id
        run.save(update_fields=["outscraper_job_id"])

    entries = extract_lead_entries(payload)
    if not entries:
        if isinstance(payload, dict):
            status = str(payload.get("status") or payload.get("Status") or "").lower()
            if status and status != "success":
                logger.info("Outscraper job %s not ready: %s", payload.get("id"), status)
                return []
        logger.warning("Unexpected Outscraper payload: %s", payload)
        return []

    created_ids = store_lead_entries(entries, city=city, run=run)
    if run is not None:
        run.total_leads = len(created_ids)
        if created_ids:
            run.expected_leads = len(created_ids)
        run.processed_leads = 0
        run.selected_leads = 0
        run.status = LeadRun.Status.PREPARING if created_ids else LeadRun.Status.READY
        update_fields = ["total_leads", "processed_leads", "selected_leads", "status"]
        if created_ids:
            update_fields.append("expected_leads")
        run.save(update_fields=update_fields)

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


def _render_template_from_db(template: EmailTemplate, lead: Lead) -> tuple[str, str, str | None]:
    """Render the subject and body strings for a stored email template."""

    fallback_url = f"https://appertivo.com{reverse('lead-landing', args=[lead.slug])}"
    context = {
        "business_name": lead.name,
        "landing_page_url": lead.landing_url or fallback_url,
        "your_name": getattr(settings, "DEFAULT_FROM_NAME", "Appertivo Team"),
    }

    def replace_tokens(value: str) -> str:
        result = value
        for key, token_value in context.items():
            result = result.replace(f"{{{{{key}}}}}", token_value or "")
        return result

    subject = replace_tokens(template.subject)
    text_body = replace_tokens(template.body_text)
    html_body = template.body_html.strip()
    if html_body:
        html_body = replace_tokens(html_body)
    else:
        html_body = None
    return subject, text_body, html_body


@shared_task(bind=True)
def send_personalized_email(self, lead_id: int, template_id: int | None = None) -> int:
    """Send a personalized outreach email to a lead."""

    lead = Lead.objects.get(pk=lead_id)
    if not lead.email:
        logger.info("Lead %s has no email; skipping outreach", lead_id)
        return lead_id

    template: EmailTemplate | None = None
    if template_id is not None:
        template = EmailTemplate.objects.filter(pk=template_id, active=True).first()
    if template is None:
        template = EmailTemplate.objects.filter(active=True).order_by("-updated_at").first()

    if template is not None:
        subject, text_body, html_body = _render_template_from_db(template, lead)
    else:
        context = {"lead": lead}
        subject = f"{lead.name}, explore your Appertivo tasting demo"
        text_body = render_to_string("leads/emails/outreach.txt", context)
        html_body = render_to_string("leads/emails/outreach.html", context)

    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [lead.email])
    if html_body:
        message.attach_alternative(html_body, "text/html")
    message.send(fail_silently=False)

    lead.emailed = True
    lead.email_bounced = False
    lead.save(update_fields=["emailed", "email_bounced"])
    return lead_id


@shared_task(bind=True)
def mark_lead_generation_complete(self, lead_id: int, run_id: int | None = None) -> int:
    """Update run progress after assets are generated for a lead."""

    if run_id is not None:
        updated = LeadRun.objects.filter(pk=run_id).update(processed_leads=F("processed_leads") + 1)
        if updated:
            run = LeadRun.objects.get(pk=run_id)
            target = run.total_leads or run.expected_leads
            if target and run.processed_leads >= target:
                run.status = LeadRun.Status.READY
                run.save(update_fields=["status"])
    return lead_id


@shared_task(bind=True)
def dispatch_lead_pipeline(
    self,
    lead_ids: Iterable[int],
    run_id: int | None = None,
    send_email: bool = True,
) -> None:
    """Kick off concept generation and optional outreach for each fetched lead."""

    run: LeadRun | None = None
    if run_id is not None:
        try:
            run = LeadRun.objects.get(pk=run_id)
        except LeadRun.DoesNotExist:
            run = None
    if run is not None:
        if not lead_ids:
            if run.status != LeadRun.Status.FETCHING:
                run.status = LeadRun.Status.READY
                run.processed_leads = run.total_leads
                run.save(update_fields=["status", "processed_leads"])
            return
        run.status = LeadRun.Status.PREPARING
        run.processed_leads = 0
        run.save(update_fields=["status", "processed_leads"])

    for lead_id in lead_ids:
        signature = generate_concepts_and_dishes.s(lead_id)
        if send_email:
            signature = signature | send_personalized_email.s()
        if run_id is not None:
            signature = signature | mark_lead_generation_complete.s(run_id)
        signature.delay()


def build_lead_pipeline() -> None:
    """Trigger the full fetch → generate → email pipeline."""

    fetch_leads.apply_async(link=dispatch_lead_pipeline.s(send_email=True))


def build_lead_run_pipeline(run_id: int, *, city: str | None = None, limit: int = 10) -> None:
    """Trigger a run-specific pipeline without immediate outreach emails."""

    fetch_leads.apply_async(
        kwargs={"run_id": run_id, "city": city, "limit": limit},
        link=dispatch_lead_pipeline.s(run_id=run_id, send_email=False),
    )
