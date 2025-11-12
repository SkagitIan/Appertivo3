import datetime
import logging
import time

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import models, transaction
from django.utils import timezone

from gastronet.models import Restaurant, Review, CrawlLog

OUTSCRAPER_URL = "https://api.outscraper.com/maps/reviews-v3"
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Fetch recent reviews via Outscraper for restaurants due for refresh."

    def add_arguments(self, parser):
        parser.add_argument("--batch", type=int, default=50)
        parser.add_argument("--per_place_limit", type=int, default=10)
        parser.add_argument("--min_days", type=int, default=3)
        parser.add_argument("--max_days", type=int, default=90)

    def handle(self, *args, **opts):
        key = getattr(settings, "OUTSCRAPER_API_KEY", None)
        if not key:
            message = "Missing OUTSCRAPER_API_KEY"
            logger.error(message)
            self.stderr.write(message)
            return

        batch = opts["batch"]
        per_place = opts["per_place_limit"]
        min_days = opts["min_days"]
        max_days = opts["max_days"]

        logger.info(
            "Starting review fetch batch_size=%s per_place_limit=%s min_days=%s max_days=%s",
            batch,
            per_place,
            min_days,
            max_days,
        )

        log = CrawlLog.objects.using("gastronet").create(
            task="fetch_reviews", scope=f"batch={batch}"
        )

        now = timezone.now()
        qs = (
            Restaurant.objects.using("gastronet")
            .filter(active=True)
            .filter(models.Q(next_fetch_at__lte=now) | models.Q(next_fetch_at__isnull=True))
            .order_by("next_fetch_at")[:batch]
        )

        headers = {"X-API-KEY": key}
        seen = 0

        for restaurant in qs:
            seen += 1
            place_id = restaurant.place_id
            if not place_id:
                log.skip_count += 1
                logger.debug("Skipping restaurant %s due to missing place_id", restaurant.pk)
                continue

            params = {"placeId": place_id, "limit": per_place, "sort": "newest"}
            try:
                res = requests.get(OUTSCRAPER_URL, headers=headers, params=params, timeout=30)
                res.raise_for_status()
                logger.info("Fetched reviews for place_id=%s", place_id)
                payload = res.json()
                core = None
                if isinstance(payload, list):
                    first = payload[0] if payload else None
                    if isinstance(first, list):
                        core = first[0] if first else None
                    elif isinstance(first, dict):
                        core = first
                elif isinstance(payload, dict):
                    core = payload

                rows = (core.get("reviews_data") or []) if core else []
                inserted = 0
                max_dt = restaurant.last_review_date

                with transaction.atomic(using="gastronet"):
                    for row in rows:
                        text = row.get("text") or ""
                        review_id = str(
                            row.get("review_id") or row.get("reviewId") or row.get("id") or ""
                        )
                        rating = row.get("rating")
                        timestamp = row.get("time") or row.get("timestamp")
                        created = (
                            datetime.datetime.fromtimestamp(timestamp, tz=timezone.utc)
                            if timestamp
                            else timezone.now()
                        )

                        if not text or not review_id:
                            continue

                        obj, created_flag = Review.objects.using("gastronet").get_or_create(
                            restaurant=restaurant,
                            source="google",
                            review_id=review_id,
                            defaults={"text": text, "rating": rating, "created_at": created},
                        )

                        if created_flag:
                            inserted += 1
                            log.success_count += 1
                            if not max_dt or created > max_dt:
                                max_dt = created
                        else:
                            log.skip_count += 1

                    review_qs = (
                        restaurant.reviews.using("gastronet")
                        .order_by("-created_at")
                        .values_list("created_at", flat=True)[:15]
                    )
                    recent = list(review_qs)
                    if inserted > 0:
                        restaurant.last_review_date = max_dt
                        gaps = [
                            (recent[i] - recent[i + 1]).total_seconds() / 86400.0
                            for i in range(len(recent) - 1)
                        ]
                        avg_gap = max(sum(gaps) / len(gaps), 1.0) if gaps else 14.0
                        restaurant.avg_review_gap_days = avg_gap
                        days = max(min_days, min(max_days, 0.8 * avg_gap))
                    else:
                        days = max(
                            min_days,
                            min(max_days, (restaurant.avg_review_gap_days or 21.0) * 1.25),
                        )

                    restaurant.next_fetch_at = timezone.now() + datetime.timedelta(days=days)
                    restaurant.save(
                        using="gastronet",
                        update_fields=[
                            "last_review_date",
                            "avg_review_gap_days",
                            "next_fetch_at",
                        ],
                    )

                log.api_calls += 1
                log.est_cost_usd += max(0, inserted - 500) * 0.03
                time.sleep(0.5)

            except Exception as exc:
                log.error_count += 1
                restaurant.next_fetch_at = timezone.now() + datetime.timedelta(days=1)
                restaurant.save(using="gastronet", update_fields=["next_fetch_at"])
                log.notes = (log.notes or "") + f"\n{restaurant.place_id}: {exc}"
                logger.exception("Failed fetching reviews for place_id=%s: %s", restaurant.place_id, exc)

        log.notes = (log.notes or "") + f"\nprocessed={seen}"
        log.ended_at = timezone.now()
        log.save(
            update_fields=[
                "success_count",
                "skip_count",
                "error_count",
                "api_calls",
                "est_cost_usd",
                "notes",
                "ended_at",
            ]
        )
        logger.info(
            "Reviews fetch complete seen=%s success=%s errors=%s",
            seen,
            log.success_count,
            log.error_count,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Reviews fetch complete. seen={seen} new_reviews={log.success_count} errors={log.error_count}"
            )
        )
