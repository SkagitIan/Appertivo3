"""Public Stripe billing views for marketing flows."""

from __future__ import annotations

import logging
import os
from typing import Any

from dotenv import load_dotenv
from django.conf import settings
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseServerError,
)
from django.views.decorators.http import require_POST
import stripe

load_dotenv()

logger = logging.getLogger(__name__)


def _ensure_api_key() -> str:
    """Ensure Stripe's API key is set and return it."""

    api_key = os.getenv("STRIPE_API_KEY") or getattr(settings, "STRIPE_SECRET_KEY", "")
    if api_key and stripe.api_key != api_key:
        stripe.api_key = api_key
    return api_key


def _marketing_domain(request: HttpRequest) -> str:
    """Return the base marketing domain for redirects."""

    domain = (
        os.getenv("DOMAIN")
        or getattr(settings, "MARKETING_DOMAIN", "")
        or getattr(settings, "SITE_URL", "")
    )
    if domain:
        return domain.rstrip("/")
    return request.build_absolute_uri("/").rstrip("/")


def _see_other(location: str) -> HttpResponse:
    """Return a HTTP 303 response to the provided location."""

    response = HttpResponse(status=303)
    response["Location"] = location
    return response


@require_POST
def create_checkout_session(request: HttpRequest) -> HttpResponse:
    """Start a Stripe Checkout session for the self-serve subscription."""

    api_key = _ensure_api_key()
    price_id = getattr(settings, "STRIPE_PRICE_ID", "")

    if not api_key or not price_id:
        logger.warning("Stripe configuration missing for checkout.")
        return HttpResponseServerError("payments_unavailable")

    domain = _marketing_domain(request)
    session_kwargs: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "consent_collection": {"terms_of_service": "required"},
        "customer_creation": "always",
        "allow_promotion_codes": True,
        "payment_method_collection": "always",
        "success_url": f"{domain}/setup?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{domain}/pricing",
    }

    try:
        session = stripe.checkout.Session.create(**session_kwargs)
    except stripe.error.StripeError:
        logger.exception("Unable to create Stripe Checkout session", exc_info=True)
        return HttpResponseServerError("checkout_unavailable")

    session_url = getattr(session, "url", "")
    if not session_url:
        logger.error("Stripe Checkout session created without a URL")
        return HttpResponseServerError("checkout_unavailable")

    return _see_other(session_url)


@require_POST
def create_billing_portal_session(request: HttpRequest) -> HttpResponse:
    """Create a Stripe Billing Portal session and redirect the customer."""

    api_key = _ensure_api_key()
    customer_id = request.POST.get("customer_id", "")

    if not api_key:
        logger.warning("Stripe configuration missing for billing portal.")
        return HttpResponseServerError("payments_unavailable")

    if not customer_id:
        return HttpResponseBadRequest("missing_customer_id")

    domain = _marketing_domain(request)
    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{domain}/setup",
        )
    except stripe.error.StripeError:
        logger.exception("Unable to create Stripe billing portal session", exc_info=True)
        return HttpResponseServerError("billing_portal_unavailable")

    portal_url = getattr(portal_session, "url", "")
    if not portal_url:
        logger.error("Stripe billing portal session created without a URL")
        return HttpResponseServerError("billing_portal_unavailable")

    return _see_other(portal_url)
