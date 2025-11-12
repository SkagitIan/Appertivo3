import logging
import os

import requests
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from dotenv import load_dotenv

from gastronet.models import Restaurant

load_dotenv()

logger = logging.getLogger(__name__)

OUTSCRAPER_API_KEY = os.getenv("OUTSCRAPER_API_KEY", "")
OUTSCRAPER_BASE_URL = "https://api.outscraper.com/maps/search-v2"  # v2 = updated endpoint

class Command(BaseCommand):
    help = "Fetch restaurants from Outscraper and save to the Restaurant model"

    def add_arguments(self, parser):
        parser.add_argument("--query", type=str, default="restaurants, Skagit County, WA")
        parser.add_argument("--limit", type=int, default=10)

    def handle(self, *args, **options):
        if not OUTSCRAPER_API_KEY:
            message = "Missing OUTSCRAPER_API_KEY in environment."
            logger.error(message)
            self.stderr.write(self.style.ERROR(message))
            return

        query = options["query"]
        limit = options["limit"]

        headers = {"X-API-KEY": OUTSCRAPER_API_KEY}
        params = {
            "query": query,
            "limit": limit,
            "dropDuplicates": "true",
            "async": "false",
        }

        logger.info("Fetching restaurant data for query=%s", query)
        self.stdout.write(f"Fetching data for: {query}")
        response = requests.get(OUTSCRAPER_BASE_URL, headers=headers, params=params)

        if response.status_code != 200:
            logger.error(
                "Outscraper responded with %s: %s", response.status_code, response.text
            )
            self.stderr.write(self.style.ERROR(f"Error {response.status_code}: {response.text}"))
            return

        data = response.json().get("data", [])
        if not data:
            logger.warning("No restaurant data returned for query=%s", query)
            self.stderr.write(self.style.WARNING("No data returned."))
            return

        records = data[0] if isinstance(data[0], list) else data

        for item in records:
            try:
                place_id = item.get("place_id")
                name = item.get("name")
                lat = item.get("latitude")
                lon = item.get("longitude")
                summary = item.get("description", "")
                city = item.get("city") or item.get("state")
                website = item.get("site")
                phone = item.get("phone")
                rating = item.get("rating")
                reviews = item.get("reviews")
                category = item.get("category")

                if not name or not lat or not lon:
                    continue

                obj, created = Restaurant.objects.update_or_create(
                    name=name,
                    defaults={
                        "place_id": place_id,   
                        "address": item.get("full_address"),
                        "city": city,
                        "website": website,
                        "phone": phone,
                        "rating": rating,
                        "review_count": reviews,
                        "category": category,
                        "cuisine": item.get("type"),
                        #"summary": summary,
                        "source": "outscraper",
                        "location": Point(lon, lat),
                    },
                )

                status = "created" if created else "updated"
                logger.info("Restaurant %s (%s)", name, status)
                self.stdout.write(f"✓ {name} ({status})")

            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("Error processing restaurant payload: %s", exc)
                self.stderr.write(self.style.ERROR(f"Error processing item: {exc}"))

        logger.info("Restaurant import finished for query=%s", query)
        self.stdout.write(self.style.SUCCESS("✅ Done. Restaurants updated."))
