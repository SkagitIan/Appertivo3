"""Webhook endpoint used by Outscraper callbacks."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from app import models, onboarding

logger = logging.getLogger(__name__)


def _extract_primary_place(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first place dictionary from the Outscraper payload."""

    data = payload.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, list) and first:
            candidate = first[0]
        elif isinstance(first, dict):
            candidate = first
        else:
            candidate = None
        if isinstance(candidate, dict):
            return candidate
    return None


def _load_results(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Fetch Outscraper results from the payload or direct body."""

    if payload.get("data"):
        return payload

    results_url = payload.get("results_location")
    if not results_url:
        return None

    try:
        response = requests.get(
            results_url,
            headers={"X-API-KEY": os.getenv("OUTSCRAPER_API_KEY", "")},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # pragma: no cover - network guard
        logger.exception("Failed to download Outscraper results: %s", exc)
        return None


@csrf_exempt
def outscraper_webhook(request, restaurant_id: str, token: str):
    """Handle Outscraper webhook callbacks with token verification."""

    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Only POST allowed"}, status=405)

    if not onboarding.verify_restaurant_token(token, restaurant_id):
        logger.warning("Rejected Outscraper webhook for restaurant %s due to bad token", restaurant_id)
        return JsonResponse({"status": "error", "message": "Invalid signature"}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    results_payload = _load_results(payload)
    if not isinstance(results_payload, dict):
        return JsonResponse({"status": "error", "message": "Missing results"}, status=400)

    restaurant = models.Restaurant.objects.filter(id=restaurant_id).first()
    if not restaurant:
        return JsonResponse({"status": "error", "message": "Restaurant not found"}, status=404)

    place_data = _extract_primary_place(results_payload)
    if not isinstance(place_data, dict):
        return JsonResponse({"status": "error", "message": "Invalid place data"}, status=400)

    restaurant.reviews_json = results_payload
    update_fields = ["reviews_json"]

    rating = place_data.get("rating")
    review_count = place_data.get("reviews_count") or place_data.get("reviews")

    if rating is not None:
        restaurant.rating = rating
        update_fields.append("rating")
    if review_count is not None:
        restaurant.review_count = review_count
        update_fields.append("review_count")

    restaurant.save(update_fields=update_fields)

    onboarding_record = models.Onboarding.objects.filter(restaurant=restaurant).first()
    if onboarding_record:
        job_id = str(
            payload.get("id")
            or payload.get("job_id")
            or payload.get("task_id")
            or ""
        )
        if job_id and onboarding_record.outscraper_reviews_job_id == job_id:
            logger.info(
                "Duplicate Outscraper webhook ignored",
                extra={"onboarding": str(onboarding_record.id), "job": job_id},
            )
            return JsonResponse({"status": "ok", "message": "Duplicate"})

        updates = ["reviews_json", "updated_at"]
        onboarding_record.reviews_json = results_payload
        if job_id:
            onboarding_record.outscraper_reviews_job_id = job_id
            updates.append("outscraper_reviews_job_id")
        onboarding_record.save(update_fields=updates)

        if onboarding.STATE_INDEX[onboarding_record.state] < onboarding.STATE_INDEX[
            models.Onboarding.State.REVIEWS_DONE
        ]:
            onboarding_record.mark(
                models.Onboarding.State.REVIEWS_DONE,
                progress=60,
                message="Reviews webhook received",
            )

    return JsonResponse({"status": "ok"})
