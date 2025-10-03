"""Workflow helpers for the onboarding pipeline."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import os

import requests
from celery import chain, shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.urls import reverse

from . import models
from .outscraper import queue_outscraper_payload
from .tasks import run_outscraper_search

logger = logging.getLogger(__name__)

_SIGNER = TimestampSigner()


def _load_job(job_id: str) -> models.ProvisioningJob:
    return models.ProvisioningJob.objects.select_related(
        "onboarding", "onboarding__restaurant"
    ).get(id=job_id)


def _payload(onboarding_id: str, job_id: str) -> dict[str, str]:
    return {"onboarding_id": onboarding_id, "job_id": job_id}


def _ensure_payload(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return {"onboarding_id": str(value[0]), "job_id": str(value[1])}
    return {"onboarding_id": str(value), "job_id": ""}


def _update_job(
    job: models.ProvisioningJob,
    *,
    status: str | None = None,
    current_step: str | None = None,
    error: str | None = None,
    finished: bool | None = None,
    extra_fields: Optional[dict[str, Any]] = None,
) -> None:
    fields: list[str] = []
    if status and job.status != status:
        job.status = status
        fields.append("status")
    if current_step is not None and job.current_step != current_step:
        job.current_step = current_step
        fields.append("current_step")
    if error is not None:
        normalized = (error or "")[:2000]
        if job.error != normalized:
            job.error = normalized
            fields.append("error")
    if finished is True:
        if not job.finished_at:
            job.finished_at = timezone.now()
            fields.append("finished_at")
    elif finished is False and job.finished_at is not None:
        job.finished_at = None
        fields.append("finished_at")
    if extra_fields:
        for name, value in extra_fields.items():
            setattr(job, name, value)
            fields.append(name)
    if fields:
        job.save(update_fields=list(dict.fromkeys(fields)))


def _build_web_profile(onboarding: models.Onboarding) -> dict[str, Any]:
    restaurant = onboarding.restaurant
    summary_bits = []
    if restaurant.description:
        summary_bits.append(restaurant.description)
    if restaurant.location_text:
        summary_bits.append(f"Located in {restaurant.location_text}.")
    if restaurant.phone:
        summary_bits.append(f"Call us at {restaurant.phone}.")
    if not summary_bits:
        summary_bits.append(f"{restaurant.name} is getting set up on Appertivo.")
    return {
        "generated_at": timezone.now().isoformat(),
        "name": restaurant.name,
        "website": restaurant.website or "",
        "summary": " ".join(summary_bits),
        "menu_urls": list(restaurant.menu_urls or []),
    }


def _analyze_reviews(onboarding: models.Onboarding) -> dict[str, Any]:
    reviews_payload = onboarding.reviews_json or {}
    reviews = []
    if isinstance(reviews_payload, dict):
        data = reviews_payload.get("data")
        if isinstance(data, list) and data:
            candidates = data[0] if isinstance(data[0], list) else data
            if isinstance(candidates, list):
                for item in candidates:
                    if isinstance(item, dict):
                        reviews.append(item)
    themes: list[str] = []
    if reviews:
        texts = [item.get("review_text", "") for item in reviews[:5]]
        for text in texts:
            lower = text.lower()
            if "service" in lower and "service" not in themes:
                themes.append("Friendly service")
            if "food" in lower and "food quality" not in themes:
                themes.append("Food quality")
            if "atmosphere" in lower and "cozy atmosphere" not in themes:
                themes.append("Cozy atmosphere")
    if not themes:
        themes = ["Gathering initial impressions"]
    return {
        "generated_at": timezone.now().isoformat(),
        "themes": themes,
        "review_sample": reviews[:3],
    }


def _draft_personas(onboarding: models.Onboarding) -> dict[str, Any]:
    restaurant = onboarding.restaurant
    base_city = (restaurant.location_text or "your area").split(",")[0]
    personas = [
        {
            "name": "Local Regular",
            "motivation": "Visits weekly for dependable favorites and warm service.",
        },
        {
            "name": "Weekend Explorer",
            "motivation": "Seeks standout dishes and seasonal specials in %s." % base_city,
        },
    ]
    if restaurant.review_count and restaurant.review_count > 100:
        personas.append(
            {
                "name": "Tourist Spotlight",
                "motivation": "Checks reviews before booking and values memorable experiences.",
            }
        )
    return {
        "generated_at": timezone.now().isoformat(),
        "personas": personas,
    }


def verify_recaptcha(token: str, remote_ip: str = None) -> bool:
    """Verify reCAPTCHA v3 token with Google's API.
    
    Args:
        token: The reCAPTCHA response token from the client
        remote_ip: Optional client IP address
    
    Returns:
        True if verification succeeds with score >= 0.5, False otherwise
    """
    secret_key = getattr(settings, "RECAPTCHA_SECRET_KEY", None)
    
    if not secret_key:
        logger.warning("RECAPTCHA_SECRET_KEY not configured, skipping verification")
        return True
    
    if not token:
        logger.warning("No reCAPTCHA token provided")
        return False
    
    try:
        payload = {
            "secret": secret_key,
            "response": token,
        }
        if remote_ip:
            payload["remoteip"] = remote_ip
        
        response = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data=payload,
            timeout=5
        )
        response.raise_for_status()
        result = response.json()
        
        success = result.get("success", False)
        score = result.get("score", 0.0)
        
        if not success:
            logger.warning(
                "reCAPTCHA verification failed",
                extra={"error_codes": result.get("error-codes", [])}
            )
            return False
        
        if score < 0.5:
            logger.warning(
                "reCAPTCHA score too low",
                extra={"score": score}
            )
            return False
        
        logger.info("reCAPTCHA verification successful", extra={"score": score})
        return True
        
    except requests.RequestException as e:
        logger.error("reCAPTCHA verification request failed", extra={"error": str(e)})
        return True
    except Exception as e:
        logger.error("Unexpected error during reCAPTCHA verification", extra={"error": str(e)})
        return True


def generate_activation_token(user_id: str) -> str:
    """Generate a signed activation token for a user ID."""
    return _SIGNER.sign(str(user_id))


def verify_activation_token(token: str, max_age: int = 86400) -> str | None:
    """Verify activation token and return user_id or None if invalid/expired.
    
    Args:
        token: The signed token string
        max_age: Maximum age in seconds (default 86400 = 24 hours)
    
    Returns:
        User ID string if valid, None otherwise
    """
    try:
        user_id = _SIGNER.unsign(token, max_age=max_age)
        return str(user_id)
    except (BadSignature, SignatureExpired):
        return None


STATE_SEQUENCE: List[str] = [
    models.Onboarding.State.CREATED,
    models.Onboarding.State.EMAIL_CONFIRMED,
    models.Onboarding.State.CHECKOUT_STARTED,
    models.Onboarding.State.CHECKOUT_PAID,
    models.Onboarding.State.SCRAPE_QUEUED,
    models.Onboarding.State.SCRAPE_DONE,
    models.Onboarding.State.REVIEWS_QUEUED,
    models.Onboarding.State.REVIEWS_DONE,
    models.Onboarding.State.WEB_ANALYSIS_DONE,
    models.Onboarding.State.REVIEW_ANALYSIS_DONE,
    models.Onboarding.State.PERSONAS_DONE,
    models.Onboarding.State.COMPLETE,
    models.Onboarding.State.FAILED,
]
STATE_INDEX = {value: index for index, value in enumerate(STATE_SEQUENCE)}

STATUS_MESSAGES: Dict[str, List[str]] = {
    models.Onboarding.State.CREATED: ["Waiting for email confirmation…"],
    models.Onboarding.State.EMAIL_CONFIRMED: ["Reviewing consent forms…"],
    models.Onboarding.State.CHECKOUT_STARTED: ["Awaiting Stripe confirmation…"],
    models.Onboarding.State.CHECKOUT_PAID: ["Queuing data fetch…"],
    models.Onboarding.State.SCRAPE_QUEUED: ["Searching for restaurant footprint…"],
    models.Onboarding.State.SCRAPE_DONE: ["Search completed. Preparing reviews fetch…"],
    models.Onboarding.State.REVIEWS_QUEUED: ["Fetching latest Google reviews…"],
    models.Onboarding.State.REVIEWS_DONE: ["Reviews stored. Analyzing website…"],
    models.Onboarding.State.WEB_ANALYSIS_DONE: ["Summarizing themes from reviews…"],
    models.Onboarding.State.REVIEW_ANALYSIS_DONE: ["Drafting customer personas…"],
    models.Onboarding.State.PERSONAS_DONE: ["Finalizing workspace setup…"],
    models.Onboarding.State.COMPLETE: ["All set! Redirecting you to the dashboard."],
    models.Onboarding.State.FAILED: ["Something went wrong. Retry when ready."],
}


@dataclass
class OnboardingStatus:
    """Lightweight serializable status container."""

    state: str
    progress: int
    messages: List[str]
    last_error: str
    can_retry: bool



@dataclass
class SignupResult:
    """Details created during the signup bootstrap flow."""
    user: Any
    account: models.Account
    restaurant: models.Restaurant
    onboarding: models.Onboarding

def _state_index(state: str) -> int:
    return STATE_INDEX.get(state, -1)


def _load_onboarding(onboarding_id: uuid.UUID) -> models.Onboarding:
    return models.Onboarding.objects.select_related("restaurant", "user").get(
        id=onboarding_id
    )


def ensure_onboarding_for_user(user) -> models.Onboarding:
    """Return the onboarding record for the user, creating it if missing."""

    onboarding, _ = models.Onboarding.objects.get_or_create(user=user)

    membership = (
        models.Membership.objects.filter(user=user)
        .select_related("account")
        .first()
    )
    if membership and not onboarding.restaurant:
        restaurant = (
            models.Restaurant.objects.filter(account=membership.account)
            .order_by("created_at")
            .first()
        )
        if restaurant:
            onboarding.restaurant = restaurant
            onboarding.save(update_fields=["restaurant", "updated_at"])

    if onboarding.state == models.Onboarding.State.CREATED:
        onboarding.mark(models.Onboarding.State.EMAIL_CONFIRMED, progress=5)
    return onboarding


def start_signup(
    *, email: str, password: str, restaurant_name: str, location: str
) -> SignupResult:
    """Create the core records for a new signup flow."""

    User = get_user_model()
    with transaction.atomic():
        user = User.objects.create_user(username=email, email=email, password=password)
        models.UserProfile.objects.create(user=user)
        account = models.Account.objects.create(name=restaurant_name)
        models.Membership.objects.create(
            account=account, user=user, role=models.Membership.Role.OWNER
        )
        restaurant = models.Restaurant.objects.create(
            account=account,
            name=restaurant_name,
            location_text=location,
        )
        onboarding_record, created = models.Onboarding.objects.get_or_create(
            user=user, defaults={"restaurant": restaurant}
        )
        if not created and onboarding_record.restaurant_id != restaurant.id:
            onboarding_record.restaurant = restaurant
            onboarding_record.save(update_fields=["restaurant", "updated_at"])
        
        activation_token = generate_activation_token(str(user.id))
        onboarding_record.activation_token = activation_token
        onboarding_record.save(update_fields=["activation_token", "updated_at"])

    return SignupResult(
        user=user,
        account=account,
        restaurant=restaurant,
        onboarding=onboarding_record,
    )

def record_consent(
    onboarding: models.Onboarding,
    *,
    accepted_terms: bool,
    accepted_privacy: bool,
    authorized_data_fetch: bool,
) -> None:
    """Persist consent flags and update state if complete."""

    onboarding.accepted_terms = accepted_terms
    onboarding.accepted_privacy = accepted_privacy
    onboarding.authorized_data_fetch = authorized_data_fetch
    onboarding.save(
        update_fields=[
            "accepted_terms",
            "accepted_privacy",
            "authorized_data_fetch",
            "updated_at",
        ]
    )
    if (
        accepted_terms
        and accepted_privacy
        and authorized_data_fetch
        and _state_index(onboarding.state)
        <= _state_index(models.Onboarding.State.EMAIL_CONFIRMED)
    ):
        onboarding.mark(models.Onboarding.State.EMAIL_CONFIRMED, progress=10)


def mark_checkout_started(user, checkout_url: Optional[str] = None) -> None:
    onboarding = ensure_onboarding_for_user(user)
    if _state_index(onboarding.state) < _state_index(
        models.Onboarding.State.CHECKOUT_STARTED
    ):
        message = None
        if checkout_url:
            message = f"Stripe checkout started: {checkout_url}"
        onboarding.mark(
            models.Onboarding.State.CHECKOUT_STARTED,
            progress=15,
            message=message,
        )


def mark_checkout_paid(account: models.Account) -> None:
    onboarding_ids = list(
        models.Onboarding.objects.filter(
            user__membership__account=account
        ).values_list("id", flat=True)
    )
    for onboarding_id in onboarding_ids:
        onboarding = models.Onboarding.objects.get(id=onboarding_id)
        if _state_index(onboarding.state) <= _state_index(
            models.Onboarding.State.CHECKOUT_PAID
        ):
            onboarding.mark(models.Onboarding.State.CHECKOUT_PAID, progress=25)
            kickoff_after_payment(onboarding.id)


def sign_restaurant_token(restaurant_id: uuid.UUID) -> str:
    return _SIGNER.sign(str(restaurant_id))


def verify_restaurant_token(token: str, restaurant_id: uuid.UUID) -> bool:
    try:
        value = _SIGNER.unsign(token, max_age=60 * 60 * 24)
    except (BadSignature, SignatureExpired):
        return False
    return str(value) == str(restaurant_id)


def status_for(onboarding: models.Onboarding) -> OnboardingStatus:
    messages = STATUS_MESSAGES.get(onboarding.state, [])
    return OnboardingStatus(
        state=onboarding.state,
        progress=onboarding.progress,
        messages=list(messages),
        last_error=onboarding.last_error,
        can_retry=onboarding.state == models.Onboarding.State.FAILED,
    )


def kickoff_after_payment(
    onboarding_id: uuid.UUID, job_id: uuid.UUID | None = None
) -> None:
    onboarding = (
        models.Onboarding.objects.select_related("restaurant")
        .filter(id=onboarding_id)
        .first()
    )
    if not onboarding or not onboarding.restaurant:
        return
    if _state_index(onboarding.state) >= _state_index(models.Onboarding.State.COMPLETE):
        return

    job: models.ProvisioningJob | None = None
    if job_id:
        job = models.ProvisioningJob.objects.filter(id=job_id).first()
    if not job:
        job = models.ProvisioningJob.objects.create(onboarding=onboarding)

    _update_job(
        job,
        status=models.ProvisioningJob.Status.RUNNING,
        current_step="initializing",
        error="",
        finished=False,
    )

    chain(
        task_outscraper_search.s(str(onboarding_id), str(job.id)),
        task_reviews_sync.s(),
        task_openai_profile.s(),
        task_openai_reviews.s(),
        task_openai_personas.s(),
        task_finalize.s(),
    ).apply_async()


def retry_failed(onboarding: models.Onboarding) -> None:
    if onboarding.state != models.Onboarding.State.FAILED:
        return
    onboarding.mark(models.Onboarding.State.CHECKOUT_PAID, progress=25)
    last_job = (
        onboarding.provisioning_jobs.order_by("-created_at").first()
        if hasattr(onboarding, "provisioning_jobs")
        else None
    )
    job = models.ProvisioningJob.objects.create(
        onboarding=onboarding,
        stripe_session_id=(last_job.stripe_session_id if last_job else ""),
    )
    kickoff_after_payment(onboarding.id, job.id)


@shared_task(bind=True)
def task_outscraper_search(self, onboarding_id: str, job_id: str) -> dict[str, str]:
    onboarding = _load_onboarding(onboarding_id)
    job = _load_job(job_id)

    if _state_index(onboarding.state) >= _state_index(
        models.Onboarding.State.SCRAPE_DONE
    ):
        logger.info(
            "Skipping Outscraper search; onboarding already complete",
            extra={"onboarding": str(onboarding.id)},
        )
        return _payload(onboarding_id, job_id)

    _update_job(
        job,
        status=models.ProvisioningJob.Status.RUNNING,
        current_step="outscraper_search",
        error="",
        finished=False,
    )

    try:
        onboarding.mark(
            models.Onboarding.State.SCRAPE_QUEUED,
            progress=35,
            message="Queued Outscraper search",
        )
        if not onboarding.outscraper_search_job_id:
            payload = queue_outscraper_payload(onboarding.restaurant)
            onboarding.outscraper_search_job_id = str(payload.id)
            onboarding.save(
                update_fields=["outscraper_search_job_id", "updated_at"]
            )
            api_key = getattr(
                settings, "OUTSCRAPER_API_KEY", os.getenv("OUTSCRAPER_API_KEY")
            )
            if api_key:
                logger.info(
                    "Dispatching Outscraper search", extra={"payload": str(payload.id)}
                )
                run_outscraper_search(payload.id)
            else:
                logger.info(
                    "Outscraper API key missing; search will use existing context",
                    extra={"onboarding": str(onboarding.id)},
                )
        onboarding.mark(
            models.Onboarding.State.SCRAPE_DONE,
            progress=45,
            message="Search completed",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Onboarding search failed",
            extra={"onboarding": onboarding.id, "job": job.id},
        )
        onboarding.fail(str(exc))
        _update_job(
            job,
            status=models.ProvisioningJob.Status.FAILED,
            current_step="outscraper_search",
            error=str(exc),
            finished=True,
        )
        raise

    return _payload(onboarding_id, job_id)


@shared_task(bind=True)
def task_reviews_sync(self, previous_step: Any) -> dict[str, str]:
    info = _ensure_payload(previous_step)
    onboarding = _load_onboarding(info["onboarding_id"])
    job = _load_job(info["job_id"])

    if _state_index(onboarding.state) >= _state_index(
        models.Onboarding.State.REVIEWS_DONE
    ):
        logger.info(
            "Skipping review sync; onboarding already advanced",
            extra={"onboarding": str(onboarding.id)},
        )
        return info

    _update_job(
        job,
        status=models.ProvisioningJob.Status.RUNNING,
        current_step="reviews",
        error="",
        finished=False,
    )

    try:
        onboarding.mark(
            models.Onboarding.State.REVIEWS_QUEUED,
            progress=55,
            message="Queued reviews refresh",
        )

        if not onboarding.reviews_json:
            onboarding.reviews_json = {
                "status": "queued",
                "generated_at": timezone.now().isoformat(),
            }
            onboarding.save(update_fields=["reviews_json", "updated_at"])

        if not onboarding.outscraper_reviews_job_id:
            api_key = getattr(
                settings, "OUTSCRAPER_API_KEY", os.getenv("OUTSCRAPER_API_KEY")
            )
            if api_key:
                token = sign_restaurant_token(onboarding.restaurant.id)
                site_url = getattr(settings, "SITE_URL", "https://app.example.com")
                webhook_path = reverse(
                    "outscraper_webhook",
                    args=[onboarding.restaurant.id, token],
                )
                webhook_url = site_url.rstrip("/") + webhook_path
                params = {
                    "query": onboarding.restaurant.google_place_id
                    or (onboarding.restaurant.context_json or {}).get("google_id")
                    or onboarding.restaurant.name
                    or onboarding.restaurant.location_text,
                    "limit": 1,
                    "reviewsLimit": 10,
                    "async": "true",
                    "webhook": webhook_url,
                    "sort": "newest",
                    "ignoreEmpty": "true",
                    "fields": "place_id,reviews_data.review_text",
                }
                headers = {"X-API-KEY": api_key}
                try:
                    response = requests.get(
                        "https://api.outscraper.cloud/google-maps-reviews",
                        params=params,
                        headers=headers,
                        timeout=10,
                    )
                    response.raise_for_status()
                    payload = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
                    job_id = str(
                        payload.get("id")
                        or payload.get("job_id")
                        or payload.get("task_id")
                        or ""
                    )
                    if job_id:
                        onboarding.outscraper_reviews_job_id = job_id
                        onboarding.save(
                            update_fields=["outscraper_reviews_job_id", "updated_at"]
                        )
                        logger.info(
                            "Queued Outscraper reviews", extra={"job": job_id}
                        )
                except requests.RequestException as exc:  # pragma: no cover
                    logger.warning(
                        "Outscraper reviews request failed", exc_info=True, extra={"onboarding": str(onboarding.id)}
                    )
            else:
                logger.info(
                    "Outscraper API key missing; skipping remote reviews fetch",
                    extra={"onboarding": str(onboarding.id)},
                )
        onboarding.mark(
            models.Onboarding.State.REVIEWS_DONE,
            progress=60,
            message="Reviews snapshot stored",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Onboarding reviews sync failed", extra={"onboarding": onboarding.id}
        )
        onboarding.fail(str(exc))
        _update_job(
            job,
            status=models.ProvisioningJob.Status.FAILED,
            current_step="reviews",
            error=str(exc),
            finished=True,
        )
        raise
    return info


@shared_task(bind=True)
def task_openai_profile(self, previous_step: Any) -> dict[str, str]:
    info = _ensure_payload(previous_step)
    onboarding = _load_onboarding(info["onboarding_id"])
    job = _load_job(info["job_id"])

    if _state_index(onboarding.state) >= _state_index(
        models.Onboarding.State.WEB_ANALYSIS_DONE
    ):
        return info

    _update_job(
        job,
        status=models.ProvisioningJob.Status.RUNNING,
        current_step="web_profile",
        error="",
        finished=False,
    )

    try:
        if not onboarding.web_profile_json:
            onboarding.web_profile_json = _build_web_profile(onboarding)
            onboarding.save(update_fields=["web_profile_json", "updated_at"])
        onboarding.mark(
            models.Onboarding.State.WEB_ANALYSIS_DONE,
            progress=75,
            message="Web profile generated",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Onboarding web analysis failed", extra={"onboarding": onboarding.id}
        )
        onboarding.fail(str(exc))
        _update_job(
            job,
            status=models.ProvisioningJob.Status.FAILED,
            current_step="web_profile",
            error=str(exc),
            finished=True,
        )
        raise
    return info


@shared_task(bind=True)
def task_openai_reviews(self, previous_step: Any) -> dict[str, str]:
    info = _ensure_payload(previous_step)
    onboarding = _load_onboarding(info["onboarding_id"])
    job = _load_job(info["job_id"])

    if _state_index(onboarding.state) >= _state_index(
        models.Onboarding.State.REVIEW_ANALYSIS_DONE
    ):
        return info

    _update_job(
        job,
        status=models.ProvisioningJob.Status.RUNNING,
        current_step="review_analysis",
        error="",
        finished=False,
    )

    try:
        if not onboarding.review_analysis_json:
            onboarding.review_analysis_json = _analyze_reviews(onboarding)
            onboarding.save(update_fields=["review_analysis_json", "updated_at"])
        onboarding.mark(
            models.Onboarding.State.REVIEW_ANALYSIS_DONE,
            progress=85,
            message="Review insights generated",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Onboarding review analysis failed", extra={"onboarding": onboarding.id}
        )
        onboarding.fail(str(exc))
        _update_job(
            job,
            status=models.ProvisioningJob.Status.FAILED,
            current_step="review_analysis",
            error=str(exc),
            finished=True,
        )
        raise
    return info


@shared_task(bind=True)
def task_openai_personas(self, previous_step: Any) -> dict[str, str]:
    info = _ensure_payload(previous_step)
    onboarding = _load_onboarding(info["onboarding_id"])
    job = _load_job(info["job_id"])

    if _state_index(onboarding.state) >= _state_index(
        models.Onboarding.State.PERSONAS_DONE
    ):
        return info

    _update_job(
        job,
        status=models.ProvisioningJob.Status.RUNNING,
        current_step="personas",
        error="",
        finished=False,
    )

    try:
        if not onboarding.personas_json:
            onboarding.personas_json = _draft_personas(onboarding)
            onboarding.save(update_fields=["personas_json", "updated_at"])
        onboarding.mark(
            models.Onboarding.State.PERSONAS_DONE,
            progress=95,
            message="Personas drafted",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Onboarding personas failed", extra={"onboarding": onboarding.id}
        )
        onboarding.fail(str(exc))
        _update_job(
            job,
            status=models.ProvisioningJob.Status.FAILED,
            current_step="personas",
            error=str(exc),
            finished=True,
        )
        raise
    return info


@shared_task(bind=True)
def task_finalize(self, previous_step: Any) -> dict[str, str]:
    info = _ensure_payload(previous_step)
    onboarding = _load_onboarding(info["onboarding_id"])
    job = _load_job(info["job_id"])

    if onboarding.state == models.Onboarding.State.COMPLETE:
        _update_job(
            job,
            status=models.ProvisioningJob.Status.SUCCEEDED,
            current_step="complete",
            error="",
            finished=True,
        )
        return info
    try:
        onboarding.mark(
            models.Onboarding.State.COMPLETE,
            progress=100,
            message="Onboarding complete",
        )
        _update_job(
            job,
            status=models.ProvisioningJob.Status.SUCCEEDED,
            current_step="complete",
            error="",
            finished=True,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Onboarding finalize failed", extra={"onboarding": onboarding.id}
        )
        onboarding.fail(str(exc))
        _update_job(
            job,
            status=models.ProvisioningJob.Status.FAILED,
            current_step="complete",
            error=str(exc),
            finished=True,
        )
        raise
    return info


@shared_task
def task_send_welcome_email(onboarding_id: str) -> None:
    """Send the activation email after payment completion."""

    onboarding = _load_onboarding(onboarding_id)
    user = onboarding.user
    if not getattr(user, "email", None):
        logger.info(
            "Skipping welcome email; user has no email",
            extra={"onboarding": str(onboarding.id)},
        )
        return

    if not onboarding.activation_token:
        onboarding.activation_token = generate_activation_token(str(user.id))
        onboarding.save(update_fields=["activation_token", "updated_at"])

    site_url = getattr(settings, "SITE_URL", "https://app.example.com").rstrip("/")
    activation_link = f"{site_url}/activate/{onboarding.activation_token}/"
    context = {"activation_link": activation_link, "user": user}
    subject = "Welcome to Appertivo"
    text_body = (
        "Welcome to Appertivo! Use the link below to access your workspace: "
        f"{activation_link}"
    )
    html_body = render_to_string("emails/activation_email.html", context)
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "hello@appertivo.com"),
        to=[user.email],
    )
    message.attach_alternative(html_body, "text/html")
    try:
        message.send()
        logger.info(
            "Welcome email sent",
            extra={"onboarding": str(onboarding.id), "user": user.email},
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Failed to send welcome email", exc_info=True, extra={"error": str(exc)}
        )
