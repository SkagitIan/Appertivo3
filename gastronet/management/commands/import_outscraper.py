import os, json
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.gis.geos import Point
from gastronet.models import Restaurant


## Example:
## python manage.py import_outscraper /home/django/appertivo/Outscraper-20251110231245xs5f_restaurant.json


class Command(BaseCommand):
    help = "Import Outscraper restaurant JSON dump into the gastronet database"

    def add_arguments(self, parser):
        parser.add_argument("path", type=str, help="Path to Outscraper JSON file")

    def handle(self, *args, **opts):
        path = opts["path"]
        if not os.path.exists(path):
            self.stderr.write(self.style.ERROR(f"File not found: {path}"))
            return

        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        # Your file is a flat list of restaurant records
        if isinstance(payload, list):
            raw_records = payload
        elif isinstance(payload, dict) and "data" in payload:
            # handle legacy nested format
            raw_records = []
            for batch in payload["data"]:
                if isinstance(batch, list):
                    raw_records.extend(batch)
                elif isinstance(batch, dict):
                    raw_records.append(batch)
        else:
            self.stderr.write(self.style.ERROR(f"Unexpected JSON structure: {type(payload)}"))
            return

        self.stdout.write(f"Found {len(raw_records)} records in file.")

        created, updated, skipped = 0, 0, 0

        def parse_review_count(value):
            """Outscraper sometimes omits review totals; normalize to int >= 0."""
            if value in (None, "", "null"):
                return 0
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return 0

        for rec in raw_records:
            pid = rec.get("place_id")
            name = rec.get("name")
            if not pid or not name:
                skipped += 1
                continue

            reviews = parse_review_count(rec.get("reviews"))

            # Create or update
            obj, created_flag = Restaurant.objects.get_or_create(
                place_id=pid,
                defaults={
                    "name": name,
                    "address": rec.get("full_address"),
                    "city": rec.get("city"),
                    "website": rec.get("site"),
                    "phone": rec.get("phone"),
                    "category": rec.get("type"),
                    "rating": rec.get("rating"),
                    "review_count": reviews,
                    "summary": rec.get("description"),
                    "latitude": rec.get("latitude"),
                    "longitude": rec.get("longitude"),
                    "menu_url": rec.get("menu_link"),
                    "source": "outscraper",
                    "created_at": timezone.now(),
                    "first_seen": timezone.now(),
                },
            )

            # Update changed fields
            changed = False
            update_fields = {}

            def maybe_update(field, value, allow_falsy=False):
                nonlocal changed
                if (value or allow_falsy) and getattr(obj, field, None) != value:
                    update_fields[field] = value
                    changed = True

            maybe_update("rating", rec.get("rating"))
            maybe_update("review_count", reviews, allow_falsy=True)
            maybe_update("website", rec.get("site"))
            maybe_update("menu_url", rec.get("menu_link"))
            maybe_update("category", rec.get("type"))
            maybe_update("summary", rec.get("description"))

            # extended fields (optional, if added to your model)
            maybe_update("price_range", rec.get("range"))
            maybe_update("logo_url", rec.get("logo"))
            maybe_update("photo_url", rec.get("photo"))
            maybe_update("reviews_url", rec.get("reviews_link"))
            maybe_update("reservation_links", rec.get("reservation_links"))
            maybe_update("owner_link", rec.get("owner_link"))
            maybe_update("hours", rec.get("working_hours"))
            maybe_update("about", rec.get("about"))

            # Set PointField if missing
            lat, lon = rec.get("latitude"), rec.get("longitude")
            if lat and lon and not obj.location:
                obj.location = Point(float(lon), float(lat))
                changed = True
                update_fields["location"] = obj.location

            if changed:
                for f, v in update_fields.items():
                    setattr(obj, f, v)
                obj.last_seen = timezone.now()
                obj.save(update_fields=list(update_fields.keys()) + ["last_seen"])
                if not created_flag:
                    updated += 1
            elif created_flag:
                created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"✅ Import complete — Created={created}, Updated={updated}, Skipped={skipped}"
            )
        )
