"""Workflow helpers for the onboarding pipeline."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from celery import chain, shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db import transaction
from django.utils import timezone

from . import models

logger = logging.getLogger(__name__)

_SIGNER = TimestampSigner()

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
        if onboarding_record.state == models.Onboarding.State.CREATED:
            onboarding_record.mark(
                models.Onboarding.State.EMAIL_CONFIRMED,
                progress=10,
                message="Signup completed",
            )

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


def mark_checkout_started(user) -> None:
    onboarding = ensure_onboarding_for_user(user)
    if _state_index(onboarding.state) < _state_index(
        models.Onboarding.State.CHECKOUT_STARTED
    ):
        onboarding.mark(models.Onboarding.State.CHECKOUT_STARTED, progress=15)


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


def kickoff_after_payment(onboarding_id: uuid.UUID) -> None:
    onboarding = models.Onboarding.objects.filter(id=onboarding_id).first()
    if not onboarding or not onboarding.restaurant:
        return
    if _state_index(onboarding.state) >= _state_index(models.Onboarding.State.COMPLETE):
        return

    chain(
        task_outscraper_search.s(str(onboarding_id)),
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
    kickoff_after_payment(onboarding.id)


@shared_task(bind=True)
def task_outscraper_search(self, onboarding_id: str) -> str:
    onboarding = _load_onboarding(onboarding_id)
    if _state_index(onboarding.state) >= _state_index(
        models.Onboarding.State.SCRAPE_DONE
    ):
        return onboarding_id

    try:
        onboarding.mark(
            models.Onboarding.State.SCRAPE_QUEUED,
            progress=35,
            message="Queued Outscraper search",
        )
        if not onboarding.outscraper_search_job_id:
            onboarding.outscraper_search_job_id = f"search-{uuid.uuid4()}"
            onboarding.save(
                update_fields=["outscraper_search_job_id", "updated_at"]
            )
        onboarding.mark(
            models.Onboarding.State.SCRAPE_DONE,
            progress=40,
            message="Search completed",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Onboarding search failed", extra={"onboarding": onboarding.id})
        onboarding.fail(str(exc))
        raise
    return onboarding_id


@shared_task(bind=True)
def task_reviews_sync(self, onboarding_id: str) -> str:
    onboarding = _load_onboarding(onboarding_id)
    if _state_index(onboarding.state) >= _state_index(
        models.Onboarding.State.REVIEWS_DONE
    ):
        return onboarding_id

    try:
        onboarding.mark(
            models.Onboarding.State.REVIEWS_QUEUED,
            progress=45,
            message="Queued reviews refresh",
        )
        # Reviews webhook will update the onboarding when data arrives.
        if not onboarding.reviews_json:
            onboarding.reviews_json = {
                "status": "pending",
                "generated_at": timezone.now().isoformat(),
            }
            onboarding.save(update_fields=["reviews_json", "updated_at"])
        onboarding.mark(
            models.Onboarding.State.REVIEWS_DONE,
            progress=55,
            message="Reviews snapshot stored",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Onboarding reviews sync failed", extra={"onboarding": onboarding.id}
        )
        onboarding.fail(str(exc))
        raise
    return onboarding_id


@shared_task(bind=True)
def task_openai_profile(self, onboarding_id: str) -> str:
    onboarding = _load_onboarding(onboarding_id)
    if _state_index(onboarding.state) >= _state_index(
        models.Onboarding.State.WEB_ANALYSIS_DONE
    ):
        return onboarding_id

    try:
        onboarding.web_profile_json = onboarding.web_profile_json or {
            "generated_at": timezone.now().isoformat(),
            "summary": "Placeholder web summary pending integration.",
        }
        onboarding.save(update_fields=["web_profile_json", "updated_at"])
        onboarding.mark(
            models.Onboarding.State.WEB_ANALYSIS_DONE,
            progress=70,
            message="Web analysis placeholder stored",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Onboarding web analysis failed", extra={"onboarding": onboarding.id}
        )
        onboarding.fail(str(exc))
        raise
    return onboarding_id


@shared_task(bind=True)
def task_openai_reviews(self, onboarding_id: str) -> str:
    onboarding = _load_onboarding(onboarding_id)
    if _state_index(onboarding.state) >= _state_index(
        models.Onboarding.State.REVIEW_ANALYSIS_DONE
    ):
        return onboarding_id

    try:
        onboarding.review_analysis_json = onboarding.review_analysis_json or {
            "generated_at": timezone.now().isoformat(),
            "themes": [
                "Friendly service",
                "Popular brunch items",
            ],
        }
        onboarding.save(update_fields=["review_analysis_json", "updated_at"])
        onboarding.mark(
            models.Onboarding.State.REVIEW_ANALYSIS_DONE,
            progress=85,
            message="Review analysis placeholder stored",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Onboarding review analysis failed", extra={"onboarding": onboarding.id}
        )
        onboarding.fail(str(exc))
        raise
    return onboarding_id


@shared_task(bind=True)
def task_openai_personas(self, onboarding_id: str) -> str:
    onboarding = _load_onboarding(onboarding_id)
    if _state_index(onboarding.state) >= _state_index(
        models.Onboarding.State.PERSONAS_DONE
    ):
        return onboarding_id

    try:
        onboarding.personas_json = onboarding.personas_json or {
            "generated_at": timezone.now().isoformat(),
            "personas": [
                {
                    "name": "Local Regular",
                    "motivation": "Comfortable dinners",
                },
                {
                    "name": "Weekend Explorer",
                    "motivation": "Seasonal specials",
                },
            ],
        }
        onboarding.save(update_fields=["personas_json", "updated_at"])
        onboarding.mark(
            models.Onboarding.State.PERSONAS_DONE,
            progress=95,
            message="Personas placeholder stored",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Onboarding personas failed", extra={"onboarding": onboarding.id}
        )
        onboarding.fail(str(exc))
        raise
    return onboarding_id


@shared_task(bind=True)
def task_finalize(self, onboarding_id: str) -> str:
    onboarding = _load_onboarding(onboarding_id)
    if onboarding.state == models.Onboarding.State.COMPLETE:
        return onboarding_id
    try:
        onboarding.mark(
            models.Onboarding.State.COMPLETE,
            progress=100,
            message="Onboarding complete",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Onboarding finalize failed", extra={"onboarding": onboarding.id}
        )
        onboarding.fail(str(exc))
        raise
    return onboarding_id
