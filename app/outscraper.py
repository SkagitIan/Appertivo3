"""Webhook endpoint used by Outscraper callbacks."""

import json
import logging
from typing import Any

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from app import models


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


@csrf_exempt
def outscraper_webhook(request):
    """Persist review payloads delivered from Outscraper."""

    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Only POST allowed"}, status=405)

    raw_body = request.body.decode("utf-8") or "{}"
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("Invalid Outscraper payload: %s", raw_body)
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    place_data = _extract_primary_place(payload)
    if not place_data:
        logger.warning("Outscraper payload missing place data: %s", payload)
        return JsonResponse({"status": "error", "message": "Missing place data"}, status=400)

    place_id = place_data.get("place_id") or place_data.get("google_id")
    if not place_id:
        logger.warning("Outscraper payload missing place identifier: %s", payload)
        return JsonResponse({"status": "error", "message": "Missing place"}, status=400)

    try:
        restaurant = models.Restaurant.objects.get(google_place_id=place_id)
    except models.Restaurant.DoesNotExist:
        logger.warning("Outscraper payload references unknown place %s", place_id)
        return JsonResponse({"status": "error", "message": "Restaurant not found"}, status=404)

    restaurant.reviews_json = payload
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
    logger.info("Stored Outscraper reviews for restaurant %s", place_id)
    return JsonResponse({"status": "ok"}, status=200)
