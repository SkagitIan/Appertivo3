"""Celery tasks for the post-payment onboarding pipeline."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Callable
from urllib.parse import urlparse
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app import llm, models
from onboarding.services import menu as menu_service
from onboarding.services import outscraper as outscraper_service
from onboarding.services import web_profile as web_profile_service
from specials.celery import app

logger = logging.getLogger(__name__)

PROGRESS_MAP = {
    models.Onboarding.State.SCRAPE_DONE: 20,
    models.Onboarding.State.REVIEWS_DONE: 35,
    models.Onboarding.State.WEB_ANALYSIS_DONE: 55,
    models.Onboarding.State.MENU_DONE: 70,
    models.Onboarding.State.REVIEW_ANALYSIS_DONE: 82,
    models.Onboarding.State.PERSONAS_DONE: 92,
    models.Onboarding.State.COMPLETE: 100,
}

_STEP_ORDER: list[tuple[str, Callable[[models.Onboarding], None]]] = []


def _now():
    return timezone.now()


def _load_onboarding(onboarding_id: UUID) -> models.Onboarding:
    return (
        models.Onboarding.objects.select_related("restaurant", "user")
        .get(id=onboarding_id)
    )


def _mark_progress(onboarding: models.Onboarding, state: str, progress: int) -> None:
    if onboarding.state == models.Onboarding.State.FAILED:
        return
    if onboarding.progress >= progress and onboarding.state == state:
        return
    if onboarding.progress > progress:
        return
    onboarding.mark(state, progress=progress)


def _job_recent(job: models.ProvisioningJob, onboarding: models.Onboarding) -> bool:
    threshold = _now() - timedelta(minutes=2)
    heartbeat = job.finished_at or onboarding.updated_at
    return bool(heartbeat and heartbeat >= threshold)


def _update_job(job: models.ProvisioningJob, step: str) -> None:
    job.current_step = step
    job.finished_at = _now()
    job.save(update_fields=["current_step", "finished_at"])


def _fallback_review_analysis(reviews: list[dict]) -> dict:
    if not reviews:
        return {
            "sentiment": "neutral",
            "average_rating": None,
            "themes": [],
            "highlights": [],
        }

    ratings = []
    texts: list[str] = []
    for review in reviews:
        rating = review.get("rating") or review.get("stars")
        if isinstance(rating, (int, float)):
            ratings.append(float(rating))
        text = review.get("text") or review.get("review_text") or ""
        if text:
            texts.append(str(text))
    avg_rating = sum(ratings) / len(ratings) if ratings else None
    sentiment = "neutral"
    if avg_rating is not None:
        if avg_rating >= 4.2:
            sentiment = "positive"
        elif avg_rating <= 3:
            sentiment = "negative"
    word_counts: Counter[str] = Counter()
    for text in texts:
        for word in json.loads(json.dumps(re.findall(r"[A-Za-z]{4,}", text.lower()))):
            word_counts[word] += 1
    themes = [word for word, _ in word_counts.most_common(5)]
    highlights = texts[:3]
    return {
        "sentiment": sentiment,
        "average_rating": avg_rating,
        "themes": themes,
        "highlights": highlights,
    }


def _log_llm_call(provider: str, model: str, step: str, function_name: str, latency_ms: int, metadata: dict) -> None:
    try:
        models.LlmCallLog.objects.create(
            provider=provider,
            model_name=model,
            call_type=models.LlmCallLog.CallType.TEXT,
            step=step,
            function_name=function_name,
            metadata=metadata,
        )
    except Exception:  # pragma: no cover
        logger.exception("Failed to persist LLM call log", exc_info=True)


def _run_review_analysis(onboarding: models.Onboarding, reviews: list[dict]) -> dict:
    if not llm.client:
        summary = _fallback_review_analysis(reviews)
        _log_llm_call("heuristic", "local", "onboarding", "review_analysis", 0, {"reviews": len(reviews)})
        return summary

    prompt = (
        "You are summarizing customer reviews for onboarding. "
        "Return JSON with keys sentiment (positive/neutral/negative), "
        "average_rating (number), themes (array of short strings), and "
        "highlights (array of <=3 review snippets)."
    )
    reviews_text = "\n".join(
        f"- {review.get('text') or review.get('review_text') or ''}" for review in reviews[:20]
    )
    started = time.monotonic()
    try:
        response = llm.client.responses.create(
            model=getattr(settings, "OPENAI_ONBOARDING_MODEL", "gpt-4.1-mini"),
            input=[{"role": "system", "content": prompt}, {"role": "user", "content": reviews_text}],
            response_format={"type": "json_object"},
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        content = getattr(response, "output_text", "")
        data = json.loads(content or "{}")
        _log_llm_call("openai", response.model, "onboarding", "review_analysis", latency_ms, {"reviews": len(reviews)})
        return data
    except Exception as exc:  # pragma: no cover - fallback if LLM unavailable
        logger.warning("Review analysis LLM failed: %s", exc)
        return _fallback_review_analysis(reviews)


def _fallback_personas(onboarding: models.Onboarding) -> list[str]:
    restaurant = onboarding.restaurant
    base_name = restaurant.name if restaurant else "Restaurant"
    location = restaurant.location_text if restaurant else "local"
    return [
        f"{base_name} Regulars",
        f"Families around {location}",
        "Foodie Adventurers",
    ]


def _run_persona_generation(onboarding: models.Onboarding) -> list[str]:
    if not llm.client:
        personas = _fallback_personas(onboarding)
        _log_llm_call("heuristic", "local", "onboarding", "personas", 0, {})
        return personas

    context = onboarding.personas_json or onboarding.web_profile_json or {}
    prompt = (
        "Return a JSON array with exactly three short persona strings for the restaurant."
    )
    started = time.monotonic()
    try:
        response = llm.client.responses.create(
            model=getattr(settings, "OPENAI_ONBOARDING_MODEL", "gpt-4.1-mini"),
            input=[{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(context)}],
            response_format={"type": "json_array"},
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        content = getattr(response, "output_text", "")
        personas = json.loads(content or "[]")
        if len(personas) != 3:
            raise ValueError("persona count mismatch")
        _log_llm_call("openai", response.model, "onboarding", "personas", latency_ms, {})
        return personas
    except Exception as exc:  # pragma: no cover - fallback ensures robustness
        logger.warning("Persona generation LLM failed: %s", exc)
        return _fallback_personas(onboarding)


def _perform_context(onboarding: models.Onboarding) -> None:
    restaurant = onboarding.restaurant
    if not restaurant:
        raise ValueError("Onboarding requires an associated restaurant")

    if restaurant.context_json and restaurant.menu_urls:
        _mark_progress(onboarding, models.Onboarding.State.SCRAPE_DONE, PROGRESS_MAP[models.Onboarding.State.SCRAPE_DONE])
        return

    context = outscraper_service.fetch_context(onboarding)
    if context:
        restaurant.context_json = context
        restaurant.google_place_id = context.get("place_id") or restaurant.google_place_id
        restaurant.phone = context.get("phone") or restaurant.phone
        restaurant.website = context.get("site") or restaurant.website
        restaurant.description = context.get("description") or restaurant.description
        restaurant.review_count = context.get("reviews") or restaurant.review_count
        restaurant.rating = context.get("rating") or restaurant.rating
        restaurant.hours_json = context.get("working_hours") or restaurant.hours_json
        restaurant.about_json = context.get("about") or restaurant.about_json
        menu_links = []
        raw_links = context.get("menu_link")
        if isinstance(raw_links, str):
            menu_links.append(raw_links)
        elif isinstance(raw_links, list):
            menu_links.extend(raw_links)
        order_links = context.get("order_links")
        if isinstance(order_links, list):
            menu_links.extend(order_links)
        restaurant.set_menu_urls(menu_links)
        restaurant.save(
            update_fields=[
                "context_json",
                "google_place_id",
                "phone",
                "website",
                "description",
                "review_count",
                "rating",
                "hours_json",
                "about_json",
                "menu_urls",
                "primary_menu_url",
            ]
        )
    _mark_progress(onboarding, models.Onboarding.State.SCRAPE_DONE, PROGRESS_MAP[models.Onboarding.State.SCRAPE_DONE])


def _perform_reviews(onboarding: models.Onboarding) -> None:
    restaurant = onboarding.restaurant
    if not restaurant:
        raise ValueError("Onboarding requires an associated restaurant")

    existing = restaurant.reviews_json or {}
    fetched_at = existing.get("fetched_at") if isinstance(existing, dict) else None
    if fetched_at:
        try:
            fetched_time = datetime.fromisoformat(fetched_at)
            if fetched_time >= (_now() - timedelta(days=30)):
                _mark_progress(
                    onboarding,
                    models.Onboarding.State.REVIEWS_DONE,
                    PROGRESS_MAP[models.Onboarding.State.REVIEWS_DONE],
                )
                return
        except ValueError:
            pass

    place_id = restaurant.google_place_id
    if not place_id and isinstance(restaurant.context_json, dict):
        place_id = restaurant.context_json.get("place_id")

    reviews = []
    if place_id:
        reviews = outscraper_service.fetch_reviews(place_id)

    payload = {
        "fetched_at": _now().isoformat(),
        "reviews": reviews,
    }
    restaurant.reviews_json = payload
    restaurant.save(update_fields=["reviews_json"])
    onboarding.reviews_json = payload
    onboarding.save(update_fields=["reviews_json"])
    _mark_progress(onboarding, models.Onboarding.State.REVIEWS_DONE, PROGRESS_MAP[models.Onboarding.State.REVIEWS_DONE])


def _perform_web_profile(onboarding: models.Onboarding) -> None:
    if onboarding.web_profile_json:
        _mark_progress(onboarding, models.Onboarding.State.WEB_ANALYSIS_DONE, PROGRESS_MAP[models.Onboarding.State.WEB_ANALYSIS_DONE])
        return

    restaurant = onboarding.restaurant
    allowed_domains: list[str] = []
    website = (restaurant.website or "").strip() if restaurant else ""

    def _maybe_add(domain: str) -> None:
        if not domain:
            return
        parsed = domain if "://" in domain else f"https://{domain}"
        host = urlparse(parsed).netloc.lower()
        if any(bad in host for bad in ("linktr", "linktree", "instagram", "facebook", "bit.ly")):
            return
        if domain not in allowed_domains:
            allowed_domains.append(domain)

    if website:
        _maybe_add(website)
    if not allowed_domains and isinstance(restaurant.context_json, dict):
        site = restaurant.context_json.get("site")
        if site:
            _maybe_add(site)

    profile = web_profile_service.build_profile(onboarding, allowed_domains)
    onboarding.web_profile_json = profile
    onboarding.save(update_fields=["web_profile_json"])
    _mark_progress(onboarding, models.Onboarding.State.WEB_ANALYSIS_DONE, PROGRESS_MAP[models.Onboarding.State.WEB_ANALYSIS_DONE])


def _perform_menu(onboarding: models.Onboarding) -> None:
    restaurant = onboarding.restaurant
    if restaurant and restaurant.active_menu_version:
        _mark_progress(
            onboarding,
            models.Onboarding.State.MENU_DONE,
            PROGRESS_MAP[models.Onboarding.State.MENU_DONE],
        )
        return

    version = menu_service.snapshot_and_normalize(onboarding)
    if not onboarding.restaurant.active_menu_version:
        onboarding.restaurant.active_menu_version = version
        onboarding.restaurant.save(update_fields=["active_menu_version"])
    _mark_progress(
        onboarding,
        models.Onboarding.State.MENU_DONE,
        PROGRESS_MAP[models.Onboarding.State.MENU_DONE],
    )


def _perform_review_analysis(onboarding: models.Onboarding) -> None:
    if onboarding.review_analysis_json:
        _mark_progress(onboarding, models.Onboarding.State.REVIEW_ANALYSIS_DONE, PROGRESS_MAP[models.Onboarding.State.REVIEW_ANALYSIS_DONE])
        return

    restaurant = onboarding.restaurant
    reviews_payload = restaurant.reviews_json if restaurant else {}
    reviews = []
    if isinstance(reviews_payload, dict):
        reviews = reviews_payload.get("reviews") or []
    analysis = _run_review_analysis(onboarding, reviews)
    onboarding.review_analysis_json = analysis
    onboarding.save(update_fields=["review_analysis_json"])
    _mark_progress(onboarding, models.Onboarding.State.REVIEW_ANALYSIS_DONE, PROGRESS_MAP[models.Onboarding.State.REVIEW_ANALYSIS_DONE])


def _perform_personas(onboarding: models.Onboarding) -> None:
    if onboarding.personas_json:
        _mark_progress(onboarding, models.Onboarding.State.PERSONAS_DONE, PROGRESS_MAP[models.Onboarding.State.PERSONAS_DONE])
        return

    personas = _run_persona_generation(onboarding)
    personas = personas[:3] if len(personas) >= 3 else (personas + _fallback_personas(onboarding))[:3]
    onboarding.personas_json = personas
    onboarding.save(update_fields=["personas_json"])
    _mark_progress(onboarding, models.Onboarding.State.PERSONAS_DONE, PROGRESS_MAP[models.Onboarding.State.PERSONAS_DONE])


def _perform_finalize(onboarding: models.Onboarding) -> None:
    restaurant = onboarding.restaurant
    if not restaurant:
        raise ValueError("Onboarding requires an associated restaurant")

    settings_obj, created = models.RestaurantSettings.objects.get_or_create(
        restaurant=restaurant,
        defaults={"default_currency": onboarding.default_currency},
    )
    if not created and settings_obj.default_currency != onboarding.default_currency:
        settings_obj.default_currency = onboarding.default_currency
        settings_obj.save(update_fields=["default_currency"])

    payload = {
        "onboarding_id": str(onboarding.id),
        "restaurant_id": str(restaurant.id),
        "status": "complete",
    }
    if not models.Notification.objects.filter(
        user=onboarding.user,
        type=models.Notification.Type.JOB_COMPLETE,
        payload__onboarding_id=str(onboarding.id),
    ).exists():
        models.Notification.objects.create(
            user=onboarding.user,
            type=models.Notification.Type.JOB_COMPLETE,
            channel=models.Notification.Channel.IN_APP,
            payload=payload,
            status=models.Notification.Status.QUEUED,
        )

    _mark_progress(onboarding, models.Onboarding.State.COMPLETE, PROGRESS_MAP[models.Onboarding.State.COMPLETE])


_STEP_ORDER = [
    ("outscraper_context", _perform_context),
    ("outscraper_reviews", _perform_reviews),
    ("web_profile", _perform_web_profile),
    ("menu_snapshot", _perform_menu),
    ("review_analysis", _perform_review_analysis),
    ("personas", _perform_personas),
    ("finalize", _perform_finalize),
]


@app.task(bind=True)
def provision_onboarding(self, provisioning_job_id: UUID) -> None:
    try:
        with transaction.atomic():
            job = (
                models.ProvisioningJob.objects.select_for_update()
                .select_related("onboarding")
                .get(id=provisioning_job_id)
            )
            onboarding = (
                models.Onboarding.objects.select_for_update()
                .select_related("restaurant", "user")
                .get(id=job.onboarding_id)
            )
            if job.status == models.ProvisioningJob.Status.SUCCEEDED:
                return
            if job.status == models.ProvisioningJob.Status.RUNNING and _job_recent(job, onboarding):
                logger.info("Provisioning job %s already running", job.id)
                return
            job.status = models.ProvisioningJob.Status.RUNNING
            job.current_step = "start"
            job.error = ""
            job.finished_at = _now()
            job.save(update_fields=["status", "current_step", "error", "finished_at"])
        # end atomic
        for step_name, func in _STEP_ORDER:
            with transaction.atomic():
                job = models.ProvisioningJob.objects.select_for_update().get(id=provisioning_job_id)
                onboarding = (
                    models.Onboarding.objects.select_for_update()
                    .select_related("restaurant", "user")
                    .get(id=job.onboarding_id)
                )
                _update_job(job, step_name)
                logger.info(
                    "Onboarding %s running step %s",
                    onboarding.id,
                    step_name,
                    extra={"job_id": str(job.id)},
                )
                func(onboarding)
                logger.info(
                    "Onboarding %s finished step %s",
                    onboarding.id,
                    step_name,
                    extra={"job_id": str(job.id)},
                )
        with transaction.atomic():
            job = models.ProvisioningJob.objects.select_for_update().get(id=provisioning_job_id)
            onboarding = (
                models.Onboarding.objects.select_for_update()
                .select_related("restaurant", "user")
                .get(id=job.onboarding_id)
            )
            if onboarding.progress < 100:
                _mark_progress(onboarding, models.Onboarding.State.COMPLETE, PROGRESS_MAP[models.Onboarding.State.COMPLETE])
            job.status = models.ProvisioningJob.Status.SUCCEEDED
            job.finished_at = _now()
            job.save(update_fields=["status", "finished_at"])
            logger.info(
                "Onboarding %s provisioning complete",
                onboarding.id,
                extra={"job_id": str(job.id)},
            )
    except Exception as exc:  # pragma: no cover - orchestrator level guard
        logger.exception("Provisioning job %s failed", provisioning_job_id)
        with transaction.atomic():
            try:
                job = models.ProvisioningJob.objects.select_for_update().get(id=provisioning_job_id)
                onboarding = (
                    models.Onboarding.objects.select_for_update()
                    .select_related("restaurant", "user")
                    .get(id=job.onboarding_id)
                )
            except models.ProvisioningJob.DoesNotExist:
                return
            onboarding.fail(str(exc))
            job.status = models.ProvisioningJob.Status.FAILED
            job.error = str(exc)
            job.finished_at = _now()
            job.save(update_fields=["status", "error", "finished_at"])
        raise


@app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=2,
    retry_backoff_max=60,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def step_fetch_outscraper_context(self, onboarding_id: UUID) -> None:
    onboarding = _load_onboarding(onboarding_id)
    _perform_context(onboarding)


@app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=2,
    retry_backoff_max=60,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def step_fetch_outscraper_reviews(self, onboarding_id: UUID) -> None:
    onboarding = _load_onboarding(onboarding_id)
    _perform_reviews(onboarding)


@app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=2,
    retry_backoff_max=60,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def step_analyze_site_web_profile(self, onboarding_id: UUID) -> None:
    onboarding = _load_onboarding(onboarding_id)
    _perform_web_profile(onboarding)


@app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=2,
    retry_backoff_max=60,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def step_persist_menu_and_ingredients(self, onboarding_id: UUID) -> None:
    onboarding = _load_onboarding(onboarding_id)
    _perform_menu(onboarding)


@app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=2,
    retry_backoff_max=60,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def step_review_analysis(self, onboarding_id: UUID) -> None:
    onboarding = _load_onboarding(onboarding_id)
    _perform_review_analysis(onboarding)


@app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=2,
    retry_backoff_max=60,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def step_personas_from_context(self, onboarding_id: UUID) -> None:
    onboarding = _load_onboarding(onboarding_id)
    _perform_personas(onboarding)


@app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=2,
    retry_backoff_max=60,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def step_finalize_settings_and_notify(self, onboarding_id: UUID) -> None:
    onboarding = _load_onboarding(onboarding_id)
    _perform_finalize(onboarding)
