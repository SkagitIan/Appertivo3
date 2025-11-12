import datetime
import logging

from django.core.management.base import BaseCommand
from django.db import models
from django.utils import timezone

from gastronet.models import Restaurant, CrawlLog

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Recompute next_fetch_at for all restaurants from observed review cadence."

    def add_arguments(self, parser):
        parser.add_argument("--min_days", type=float, default=3.0)
        parser.add_argument("--max_days", type=float, default=90.0)
        parser.add_argument("--alpha", type=float, default=0.8)
        parser.add_argument("--limit", type=int, default=5000)

    def handle(self, *args, **opts):
        min_days = opts["min_days"]
        max_days = opts["max_days"]
        alpha = opts["alpha"]
        limit = opts["limit"]

        logger.info(
            "Scheduling refreshes min_days=%s max_days=%s alpha=%s limit=%s",
            min_days,
            max_days,
            alpha,
            limit,
        )

        log = CrawlLog.objects.using("gastronet").create(
            task="schedule_refresh", scope=f"alpha={alpha}"
        )

        qs = (
            Restaurant.objects.using("gastronet")
            .filter(active=True)
            .order_by(models.F("next_fetch_at").asc(nulls_first=True))[:limit]
        )
        now = timezone.now()

        for restaurant in qs:
            recent = list(
                restaurant.reviews.using("gastronet")
                .order_by("-created_at")
                .values_list("created_at", flat=True)[:15]
            )
            if len(recent) < 2:
                days = max_days
            else:
                gaps = [
                    (recent[i] - recent[i + 1]).total_seconds() / 86400.0
                    for i in range(len(recent) - 1)
                ]
                avg_gap = max(sum(gaps) / len(gaps), 1.0)
                restaurant.avg_review_gap_days = avg_gap
                days = max(min_days, min(max_days, alpha * avg_gap))

            restaurant.next_fetch_at = now + datetime.timedelta(days=days)
            restaurant.save(using="gastronet", update_fields=["avg_review_gap_days", "next_fetch_at"])
            log.success_count += 1
            logger.debug(
                "Scheduled %s next_fetch_at=%s avg_gap=%s days=%s",
                restaurant.pk,
                restaurant.next_fetch_at,
                restaurant.avg_review_gap_days,
                days,
            )

        log.ended_at = timezone.now()
        log.save(update_fields=["success_count", "ended_at"])
        logger.info("Scheduled refresh for %s restaurants", log.success_count)
        self.stdout.write(self.style.SUCCESS(f"Schedules recomputed: {log.success_count} rows"))
