"""Background tasks for the app."""
from celery import shared_task

from app.integrations.google import fetch_post_metrics
from app.models import Special, SpecialDraft


@shared_task
def fetch_google_post_metrics() -> None:
    """Fetch metrics for active specials from Google."""
    specials = Special.objects.filter(
        google_post_name__isnull=False, status="active"
    )
    fetch_post_metrics(list(specials))


@shared_task
def enhance_image_task(draft_id: int) -> None:
    """Stub image enhancement that marks the draft image ready."""
    draft = SpecialDraft.objects.get(id=draft_id)
    draft.enhanced_image_url = "http://example.com/enhanced.jpg"
    draft.image_status = "ready"
    draft.save(update_fields=["enhanced_image_url", "image_status"])


@shared_task
def retool_description_task(draft_id: int) -> None:
    """Stub description polishing task."""
    draft = SpecialDraft.objects.get(id=draft_id)
    draft.description_ai = f"{draft.concept} {draft.description_user} polished".strip()
    draft.desc_status = "ready"
    draft.save(update_fields=["description_ai", "desc_status"])
