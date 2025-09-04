"""Background tasks for the app."""
from celery import shared_task

from app.integrations.google import fetch_post_metrics
from app.models import Special


@shared_task
def fetch_google_post_metrics() -> None:
    """Fetch metrics for active specials from Google."""
    specials = Special.objects.filter(
        google_post_name__isnull=False, status="active"
    )
    fetch_post_metrics(list(specials))
