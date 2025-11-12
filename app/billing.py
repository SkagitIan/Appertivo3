"""Public Stripe billing views for marketing flows."""

from __future__ import annotations

import logging
import os
from decimal import Decimal, InvalidOperation
from typing import Any
from . import tasks
from dotenv import load_dotenv
from django.conf import settings
from django.http import (
    HttpRequest,
    HttpResponse,
    JsonResponse,
    HttpResponseBadRequest,
    HttpResponseServerError,
)
from django.views.decorators.http import require_POST
import stripe
from django.contrib.auth import get_user_model
from django.views.decorators.csrf import csrf_exempt
load_dotenv()
from . import models
logger = logging.getLogger(__name__)
from .tasks import *
from django.contrib.auth import get_user_model

def _see_other(location: str) -> HttpResponse:
    """Return a HTTP 303 response to the provided location."""

    response = HttpResponse(status=303)
    response["Location"] = location
    return response


def pricing_redirect_view(request: HttpRequest) -> HttpResponse:
    """Public landing page that jumps straight into Stripe Checkout."""

    api_key = _ensure_api_key()
    price_id = getattr(settings, "STRIPE_PRICE_ID", "")

    if not api_key or not price_id:
        logger.warning("Stripe configuration missing for pricing redirect.")
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
def create_checkout_session(request: HttpRequest, onboarding_id ) -> HttpResponse:
    """Start a Stripe Checkout session for the self-serve subscription."""

    stripe.api_key = os.getenv("STRIPE_TEST_KEY")
    price_id = os.getenv("STRIPE_TEST_PRO")
    metadata: dict[str, str] = {"onboarding_id": str(onboarding_id)}
    place_details = {}
    if hasattr(request, "session"):
        place_details = (
            request.session.get("signup_place_details", {}).get(str(onboarding_id), {})
        )
    if isinstance(place_details, dict) and place_details:
        place_metadata = {
            "place_id": place_details.get("place_id"),
            "place_address": place_details.get("formatted_address"),
            "place_lat": place_details.get("latitude"),
            "place_lng": place_details.get("longitude"),
            "place_phone": place_details.get("formatted_phone_number"),
            "place_website": place_details.get("website"),
        }
        metadata.update(
            {k: str(v) for k, v in place_metadata.items() if v not in (None, "")}
        )
        if getattr(request, "session", None) is not None:
            details_store = request.session.get("signup_place_details", {})
            details_store.pop(str(onboarding_id), None)
            request.session["signup_place_details"] = details_store
            request.session.modified = True

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            metadata=metadata,
            line_items=[{"price": price_id, "quantity": 1}],
            subscription_data={
                "trial_period_days": 14,
                "trial_settings": {
                    "end_behavior": {"missing_payment_method": "cancel"},
                },
            },
            consent_collection={"terms_of_service": "required"},
            allow_promotion_codes=True,
            payment_method_collection="always",  # card required
            # Redirect paid users straight into Swipe with splash polling
            success_url=(
                "https://appertivo.com/swipe/?from=stripe&session_id={CHECKOUT_SESSION_ID}"
                f"&onboarding_id={str(onboarding_id)}"
            ),
            cancel_url=f"https://appertivo.com/pricing",
        )
        logger.info(session)
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

@csrf_exempt
@require_POST
def stripe_webhook(request: HttpRequest) -> HttpResponse:
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    secret = os.getenv("STRIPE_TEST_WEBHOOK")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        logger.warning(f"Invalid Stripe webhook: {e}")
        return HttpResponseBadRequest()

    # Only handle successful checkout completions
    if event["type"] != "checkout.session.completed":
        return HttpResponse()

    
    data_object = event["data"]["object"]
    logger.info(f"DATA OBJECT: {data_object}")
    onboarding_id = data_object.get("metadata", {}).get("onboarding_id")

    if not onboarding_id:
        logger.warning(f"No onboarding_id found in metadata: {data_object.get('id')}")
        return HttpResponse()

    metadata = data_object.get("metadata", {}) or {}
    place_id = metadata.get("place_id", "").strip()
    place_address = metadata.get("place_address", "").strip()
    place_lat = metadata.get("place_lat", "").strip()
    place_lng = metadata.get("place_lng", "").strip()
    place_phone = metadata.get("place_phone", "").strip()
    place_website = metadata.get("place_website", "").strip()

    if any([place_id, place_address, place_lat, place_lng, place_phone, place_website]):
        try:
            onboarding = models.Onboarding.objects.select_related("restaurant").get(
                uuid=onboarding_id
            )
        except models.Onboarding.DoesNotExist:
            logger.warning("Onboarding %s not found for place metadata", onboarding_id)
        else:
            restaurant = onboarding.restaurant
            if restaurant:
                update_fields: list[str] = []
                if place_id:
                    restaurant.google_place_id = place_id
                    update_fields.append("google_place_id")
                if place_address:
                    restaurant.location_text = place_address
                    update_fields.append("location_text")
                if place_phone:
                    restaurant.phone = place_phone
                    update_fields.append("phone")
                if place_website:
                    restaurant.website = place_website
                    update_fields.append("website")
                if place_lat:
                    try:
                        restaurant.latitude = Decimal(place_lat)
                        update_fields.append("latitude")
                    except (InvalidOperation, TypeError):
                        logger.warning("Invalid latitude from metadata: %s", place_lat)
                if place_lng:
                    try:
                        restaurant.longitude = Decimal(place_lng)
                        update_fields.append("longitude")
                    except (InvalidOperation, TypeError):
                        logger.warning("Invalid longitude from metadata: %s", place_lng)

                if update_fields:
                    restaurant.save(update_fields=list(dict.fromkeys(update_fields)))

    logger.info(f"Stripe webhook onboarding_id: {onboarding_id}")
    run_onboarding_pipeline.delay(onboarding_id)

    return JsonResponse({"queued": True})
