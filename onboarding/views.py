"""Views for the onboarding pipeline."""

from __future__ import annotations

import json
import logging

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from app import models
from onboarding.tasks import provision_onboarding

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def stripe_webhook(request: HttpRequest) -> HttpResponse:
    """Handle Stripe webhook callbacks and enqueue onboarding provisioning."""

    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")

    try:
        if secret:
            event = stripe.Webhook.construct_event(payload, sig_header, secret)
        else:
            event = json.loads(payload or "{}")
    except ValueError:
        logger.warning("Stripe webhook contained invalid JSON")
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe webhook failed signature verification")
        return HttpResponse(status=400)

    event_id = event.get("id")
    event_type = event.get("type", "")
    if not event_id:
        return HttpResponse(status=400)

    stripe_event, created = models.StripeWebhookEvent.objects.get_or_create(
        event_id=event_id,
        defaults={"event_type": event_type, "payload": event},
    )
    if not created:
        logger.info("Stripe webhook %s already processed", event_id)
        return JsonResponse({"queued": False})

    data_object = event.get("data", {}).get("object", {})
    if event_type != "checkout.session.completed":
        logger.info("Ignoring non checkout webhook %s", event_type)
        return JsonResponse({"queued": False})
    session_id = data_object.get("id", "")
    customer_email = (
        data_object.get("customer_email")
        or data_object.get("customer_details", {}).get("email")
    )

    if not customer_email:
        logger.warning("Stripe webhook missing customer email", extra={"event_id": event_id})
        return JsonResponse({"queued": False})

    user = get_user_model().objects.filter(email__iexact=customer_email).first()
    if not user:
        logger.warning("No user found for Stripe webhook email %s", customer_email)
        return JsonResponse({"queued": False})

    try:
        onboarding = models.Onboarding.objects.select_related("restaurant", "user").get(user=user)
    except models.Onboarding.DoesNotExist:
        logger.warning("No onboarding record for user %s", user.id)
        return JsonResponse({"queued": False})

    job, _ = models.ProvisioningJob.objects.get_or_create(
        onboarding=onboarding,
        stripe_session_id=session_id or "",
        defaults={"status": models.ProvisioningJob.Status.PENDING},
    )
    job.last_stripe_event_id = event_id
    job.save(update_fields=["last_stripe_event_id"])
    if onboarding.progress < 10:
        onboarding.mark(models.Onboarding.State.SCRAPE_QUEUED, progress=10)

    provision_onboarding.delay(job.id)
    return JsonResponse({"queued": True, "job_id": str(job.id)})


@login_required
@require_GET
def onboarding_status(request: HttpRequest, onboarding_id: str) -> JsonResponse:
    """Return onboarding progress for polling widgets."""

    try:
        onboarding = models.Onboarding.objects.select_related("user").get(id=onboarding_id)
    except models.Onboarding.DoesNotExist:
        return JsonResponse({"detail": "not_found"}, status=404)

    if request.user != onboarding.user and not request.user.is_staff:
        return JsonResponse({"detail": "forbidden"}, status=404)

    job = onboarding.provisioning_jobs.order_by("-created_at").first()
    current_step = job.current_step if job else ""
    last_error = job.error if job else ""

    return JsonResponse(
        {
            "state": onboarding.state,
            "progress": onboarding.progress,
            "current_step": current_step,
            "last_error": last_error,
        }
    )
