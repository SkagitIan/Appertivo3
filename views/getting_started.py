"""Views powering the getting started experience."""

from __future__ import annotations

import logging

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import redirect, render
from django.urls import reverse

from app import models
from app.views import _footer_articles

logger = logging.getLogger(__name__)


@login_required
def getting_started_view(request):
    """Guide provisioned users toward their first generated specials."""

    membership = (
        models.Membership.objects.filter(user=request.user)
        .select_related("account")
        .first()
    )
    if not membership:
        return redirect("setup")

    account = membership.account
    restaurant = (
        models.Restaurant.objects.filter(account=account)
        .order_by("created_at")
        .first()
    )
    if not restaurant:
        return redirect("setup")

    latest_menu = (
        models.MenuVersion.objects.filter(restaurant=restaurant)
        .order_by("-created_at")
        .first()
    )
    latest_concept = (
        models.Concept.objects.filter(restaurant=restaurant)
        .order_by("-created_at")
        .first()
    )
    dish_counts = (
        models.DishIdea.objects.filter(restaurant=restaurant)
        .aggregate(total=Count("id"))
    )
    dish_total = dish_counts.get("total") or 0

    logger.info(
        "getting_started_view.render",
        extra={
            "user_id": request.user.id,
            "restaurant_id": str(restaurant.id),
            "concepts": 1 if latest_concept else 0,
            "dishes": dish_total,
        },
    )

    steps = [
        {
            "title": "Generate a concept",
            "description": "Use the concept generator to spark a new special.",
            "cta_url": reverse("concepts-generate"),
            "cta_label": "Open concept generator",
            "completed": latest_concept is not None,
        },
        {
            "title": "Build it into your menu",
            "description": "Save concepts into your menus workspace and polish the copy.",
            "cta_url": reverse("menus"),
            "cta_label": "Go to menus",
            "completed": latest_menu is not None,
        },
        {
            "title": "Share with your guests",
            "description": "Export or copy your menu updates to share everywhere.",
            "cta_url": reverse("favorites"),
            "cta_label": "Export & share",
            "completed": dish_total > 0,
        },
    ]

    context = {
        "account": account,
        "restaurant": restaurant,
        "latest_menu": latest_menu,
        "latest_concept": latest_concept,
        "dish_total": dish_total,
        "steps": steps,
        "footer_articles": _footer_articles(),
    }
    return render(request, "getting_started.html", context)
