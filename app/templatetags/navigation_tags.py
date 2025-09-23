"""Custom template tags for navigation helpers."""

from django import template
from django.urls import reverse

from app import models

register = template.Library()


def _membership_restaurant_id(user):
    """Return the first restaurant id for the user's account."""
    membership = (
        models.Membership.objects.filter(user=user)
        .select_related("account")
        .first()
    )
    if not membership:
        return None
    return (
        models.Restaurant.objects.filter(account=membership.account)
        .order_by("created_at")
        .values_list("id", flat=True)
        .first()
    )


@register.simple_tag(takes_context=True)
def resolve_dashboard_url(context):
    """Resolve the dashboard URL including the restaurant identifier when possible."""
    # Prefer an explicit restaurant provided in the template context.
    restaurant = context.get("restaurant")
    if getattr(restaurant, "id", None):
        return reverse("dashboard", args=[restaurant.id])

    request = context.get("request")
    if not request:
        return reverse("home")

    resolver_match = getattr(request, "resolver_match", None)
    if resolver_match:
        restaurant_id = resolver_match.kwargs.get("restaurant_id")
        if restaurant_id:
            return reverse("dashboard", args=[restaurant_id])

    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        restaurant_id = _membership_restaurant_id(user)
        if restaurant_id:
            return reverse("dashboard", args=[restaurant_id])

    return reverse("home")
