"""Template helpers for the custom admin dashboard."""

from django import template


register = template.Library()


@register.filter
def admin_object_display(obj):
    """Return a readable label for an admin object."""

    if obj is None:
        return "—"
    for attr in ("name", "title", "label", "code", "slug"):
        if hasattr(obj, attr):
            value = getattr(obj, attr)
            if value:
                return value
    return str(obj)


@register.filter
def status_badge_class(value: str) -> str:
    """Map common status values to themed badge classes."""

    if not value:
        return "badge-neutral"

    normalized = str(value).lower()
    if normalized in {"succeeded", "active", "complete", "completed", "ready"}:
        return "badge-success"
    if normalized in {"running", "in_progress", "processing"}:
        return "badge-info"
    if normalized in {"failed", "inactive", "error", "cancelled"}:
        return "badge-danger"
    if normalized in {"queued", "pending", "draft"}:
        return "badge-warning"
    return "badge-neutral"

