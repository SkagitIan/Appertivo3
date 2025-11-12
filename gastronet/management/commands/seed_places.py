import os
import time
import logging
import requests
from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.utils import timezone

from gastronet.models import Restaurant, CrawlLog

logger = logging.getLogger(__name__)

# New v1 endpoint (POST)
BASE_URL = "https://places.googleapis.com/v1/places:searchText"

# Choose PRO fields you actually need. Adjust as needed.
# NOTE: Using displayName/formattedAddress/addressComponents/location triggers the Pro SKU.
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.addressComponents",
    "places.location",
    "places.primaryType",
    "places.types",
    "places.googleMapsUri",
    "nextPageToken",
])

def _extract_city(address_components):
    """
    Pull a human city name out of addressComponents (best-effort).
    """
    city = None
    try:
        for comp in address_components or []:
            types = set(comp.get("types", []))
            name = comp.get("longText") or comp.get("shortText")
            if not name:
                continue
            # Prefer 'locality', then 'postal_town', then 'administrative_area_level_2'
            if "locality" in types:
                city = name; break
            if "postal_town" in types and not city:
                city = name
            if "administrative_area_level_2" in types and not city:
                city = name
    except Exception:
        pass
    return city

class Command(BaseCommand):
    help = "Seed/refresh Restaurants from Google Places Text Search (New v1 + field mask, Pro SKU)."

    def add_arguments(self, parser):
        parser.add_argument("--query", type=str, default="restaurants in Seattle, WA")
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--pagesize", type=int, default=20)  # 1..20 for Text Search

    def handle(self, *args, **opts):
        key = os.getenv("GOOGLE_API_KEY") or getattr(settings, "GOOGLE_API_KEY", None)
        if not key:
            self.stderr.write("Missing GOOGLE_API_KEY")
            return

        query = opts["query"]
        limit = int(opts["limit"])
        page_size = max(1, min(int(opts["pagesize"]), 20))

        log = CrawlLog.objects.using("gastronet").create(task="seed_places_v1", scope=query)

        # v1 requires the field mask via HTTP header
        headers = {
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": FIELD_MASK,
            "Content-Type": "application/json",
        }

        total_seen = 0
        api_calls = 0
        page_token = None

        try:
            while True:
                body = {
                    "textQuery": query,
                    "pageSize": page_size,
                }
                if page_token:
                    body["pageToken"] = page_token

                resp = requests.post(BASE_URL, headers=headers, json=body, timeout=60)
                api_calls += 1
                resp.raise_for_status()
                payload = resp.json() or {}

                places = payload.get("places", [])
                for p in places:
                    if total_seen >= limit:
                        raise StopIteration

                    place_resource = p.get("id") or p.get("name")  # name can be "places/PLACE_ID"
                    # Normalize place_id from either id or name
                    if place_resource and place_resource.startswith("places/"):
                        place_id = place_resource.split("/", 1)[1]
                    else:
                        place_id = place_resource

                    display_name = (p.get("displayName") or {}).get("text")
                    formatted_address = p.get("formattedAddress")
                    loc = (p.get("location") or {})
                    lat = loc.get("latitude")
                    lng = loc.get("longitude")

                    if not place_id or not display_name:
                        log.skip_count += 1
                        continue

                    city = _extract_city(p.get("addressComponents"))

                    defaults = {
                        "name": display_name,
                        "address": formatted_address or "",
                        "city": city,
                        "latitude": lat,
                        "longitude": lng,
                        "active": True,
                        "source": "google",
                    }
                    if lat is not None and lng is not None:
                        defaults["location"] = Point(lng, lat)

                    # Upsert
                    _, created = Restaurant.objects.using("gastronet").update_or_create(
                        place_id=place_id,
                        defaults=defaults,
                    )
                    total_seen += 1
                    if created:
                        log.success_count += 1
                    else:
                        log.skip_count += 1

                page_token = payload.get("nextPageToken")
                if not page_token or total_seen >= limit:
                    break

                # Text Search (New) can need a short pause before next page becomes available
                time.sleep(2.0)

        except StopIteration:
            pass
        except Exception as exc:
            logger.exception("Text Search v1 error")
            log.error_count += 1
            log.notes = f"Error: {exc}"
        finally:
            log.api_calls = api_calls
            log.est_cost_usd = 0.0  # leave as-is unless you want to estimate
            log.ended_at = timezone.now()
            log.save(update_fields=[
                "success_count",
                "skip_count",
                "error_count",
                "api_calls",
                "est_cost_usd",
                "notes",
                "ended_at",
            ])
            self.stdout.write(
                self.style.SUCCESS(
                    f"Seed complete. total_seen={total_seen}, created={log.success_count}, updated_seen={log.skip_count}"
                )
            )
