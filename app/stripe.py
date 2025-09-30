"""Stripe billing and subscription helpers."""

from __future__ import annotations

import datetime
import json
import logging
from typing import Optional
from dotenv import load_dotenv
load_dotenv()
import os
from django.conf import settings
from django.utils import timezone
import stripe as stripe_sdk

from . import models

logger = logging.getLogger(__name__)
stripe = stripe_sdk


class InvalidWebhookPayload(Exception):
    """Raised when a webhook payload cannot be parsed."""


class InvalidWebhookSignature(Exception):
    """Raised when a webhook signature fails validation."""


def ensure_api_key() -> None:
    """Refresh the Stripe API key from settings for the current process."""

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


def stripe_timestamp(value: Optional[int]) -> datetime.datetime:
    """Convert a Stripe timestamp into an aware datetime."""

    if not value:
        return timezone.now()
    return datetime.datetime.fromtimestamp(value, tz=datetime.timezone.utc)


def get_default_plan() -> models.Plan:
    """Fetch or create the default plan used for subscriptions."""

    defaults = {
        "name": "Pro",
        "limits": {"concept_runs": 100, "dish_runs": 100, "price": "199"},
        "features": [
            "Unlimited menu scrapes",
            "Concept and dish generation",
            "Team collaboration",
        ],
    }
    plan, _ = models.Plan.objects.get_or_create(
        code=getattr(settings, "STRIPE_PLAN_CODE", "pro"), defaults=defaults
    )
    return plan


def latest_subscription_for_account(
    account: models.Account,
) -> Optional[models.Subscription]:
    """Return the most recent subscription for an account."""

    return (
        models.Subscription.objects.filter(account=account)
        .order_by("-created_at")
        .first()
    )


def sync_subscription(subscription_data: dict) -> Optional[models.Account]:
    """Create or update a subscription based on Stripe payload."""

    sub_id = subscription_data.get("id")
    if not sub_id:
        return None

    account: Optional[models.Account] = None
    local = models.Subscription.objects.filter(provider_sub_id=sub_id).first()
    if local:
        account = local.account

    metadata = subscription_data.get("metadata") or {}
    if not account:
        account_id = metadata.get("account_id")
        if account_id:
            account = models.Account.objects.filter(id=account_id).first()

    if not account:
        customer_id = subscription_data.get("customer")
        if customer_id:
            account = models.Account.objects.filter(
                stripe_customer_id=customer_id
            ).first()

    if not account:
        return None

    customer_id = subscription_data.get("customer")
    if customer_id and account.stripe_customer_id != customer_id:
        account.stripe_customer_id = customer_id
        account.save(update_fields=["stripe_customer_id"])

    plan = get_default_plan()
    status = subscription_data.get("status", models.Subscription.Status.TRIALING)
    defaults = {
        "plan": plan,
        "provider": models.Subscription.Provider.STRIPE,
        "provider_customer_id": customer_id or "",
        "status": status,
        "current_period_start": stripe_timestamp(
            subscription_data.get("current_period_start")
        ),
        "current_period_end": stripe_timestamp(
            subscription_data.get("current_period_end")
        ),
        "cancel_at_period_end": subscription_data.get(
            "cancel_at_period_end", False
        ),
    }

    subscription_obj, _ = models.Subscription.objects.update_or_create(
        account=account,
        provider=models.Subscription.Provider.STRIPE,
        provider_sub_id=sub_id,
        defaults=defaults,
    )
    if subscription_obj.account_id != account.id:
        subscription_obj.account = account
        subscription_obj.save(update_fields=["account"])
    return account


def create_checkout_session(
    *,
    account: models.Account,
    user_email: str,
    success_url: str,
    cancel_url: str,
    price_id: str,
    metadata: dict,
    trial_days: int,
) -> Optional[str]:
    """Create a Stripe Checkout session and return its URL."""

    if not price_id:
        return None

    ensure_api_key()
    session_kwargs = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "subscription_data": {
            "trial_period_days": trial_days,
            "metadata": metadata,
        },
        "metadata": metadata,
    }

    if account.stripe_customer_id:
        session_kwargs["customer"] = account.stripe_customer_id
    else:
        session_kwargs["customer_email"] = user_email

    try:
        session = stripe.checkout.Session.create(**session_kwargs)
    except stripe.error.StripeError:
        logger.exception("Unable to create Stripe Checkout session", exc_info=True)
        return None

    return getattr(session, "url", None)


def cancel_subscription(subscription: models.Subscription) -> None:
    """Request cancellation for the provided subscription if it is Stripe based."""

    if subscription.provider != models.Subscription.Provider.STRIPE:
        return
    if not getattr(settings, "STRIPE_SECRET_KEY", ""):
        return

    ensure_api_key()
    try:
        stripe.Subscription.modify(subscription.provider_sub_id, cancel_at_period_end=True)
    except stripe.error.StripeError:
        logger.exception("Unable to cancel Stripe subscription", exc_info=True)


def construct_webhook_event(payload: bytes, signature: str) -> dict:
    """Parse the incoming webhook payload into a Stripe event dict."""

    secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")
    if secret:
        try:
            return stripe.Webhook.construct_event(payload, signature, secret)
        except ValueError as exc:
            raise InvalidWebhookPayload from exc
        except stripe.error.SignatureVerificationError as exc:
            raise InvalidWebhookSignature from exc

    try:
        if not payload:
            return {}
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise InvalidWebhookPayload from exc


def retrieve_subscription(subscription_id: str) -> Optional[dict]:
    """Fetch a subscription from Stripe safely."""

    if not subscription_id:
        return None

    ensure_api_key()
    try:
        return stripe.Subscription.retrieve(subscription_id)
    except stripe.error.StripeError:
        logger.exception(
            "Unable to retrieve subscription %s from Stripe", subscription_id
        )
        return None


def complete_checkout_session(session_id: str) -> Optional[models.Account]:
    """Sync the subscription tied to a completed checkout session."""

    if not session_id:
        return None

    ensure_api_key()
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except stripe.error.StripeError:
        logger.exception(
            "Unable to retrieve Stripe checkout session %s", session_id,
            exc_info=True,
        )
        return None

    subscription_id = getattr(session, "subscription", None)
    if subscription_id is None and isinstance(session, dict):
        subscription_id = session.get("subscription")
    if not subscription_id:
        return None

    subscription = retrieve_subscription(subscription_id)
    if not subscription:
        return None

    account = sync_subscription(subscription)
    return account


def process_webhook_event(event: dict) -> Optional[models.Account]:
    """Process a webhook event and return the affected account when available."""

    if not isinstance(event, dict):
        return None

    event_type = event.get("type")
    data_object = (event.get("data") or {}).get("object", {})

    if event_type == "checkout.session.completed":
        subscription_id = data_object.get("subscription")
        subscription = retrieve_subscription(subscription_id)
        if subscription:
            return sync_subscription(subscription)
        return None

    if event_type in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }:
        if isinstance(data_object, dict):
            return sync_subscription(data_object)

    return None
