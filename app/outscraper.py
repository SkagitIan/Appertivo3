"""Webhook endpoint used by Outscraper callbacks."""

import json
import logging
from typing import Any
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
import logging
logger = logging.getLogger(__name__)
from app import models
from dotenv import load_dotenv
load_dotenv()
import os
import requests

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
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Only POST allowed"}, status=405)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    results_url = payload.get("results_location")
    if not results_url:
        return JsonResponse({"status": "error", "message": "Missing results URL"}, status=400)

    # fetch Outscraper results
    try:
        resp = requests.get(
            results_url,
            headers={"X-API-KEY": os.getenv('OUTSCRAPER_API_KEY')},
            timeout=30,
        )
        resp.raise_for_status()
        results_payload = resp.json()
    except Exception as e:
        logger.exception("Failed to fetch Outscraper results: %s", e)
        return JsonResponse({"status": "error", "message": "Fetch failed"}, status=502)

    # grab place_id
    data = results_payload.get("data") or []
    place_data = None
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, list) and first:
            place_data = first[0]
        elif isinstance(first, dict):
            place_data = first
    if not isinstance(place_data, dict):
        return JsonResponse({"status": "error", "message": "Invalid place data"}, status=400)

    place_id = place_data.get("place_id") or place_data.get("google_id")
    if not place_id:
        return JsonResponse({"status": "error", "message": "Missing place_id"}, status=400)

    try:
        restaurant = models.Restaurant.objects.get(google_place_id=place_id)
    except models.Restaurant.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Restaurant not found"}, status=404)

    # update restaurant
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
    return HttpResponse("")
