"""Views for the leads landing experiences."""
from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .models import Lead


def lead_landing(request: HttpRequest, slug: str) -> HttpResponse:
    """Render the personalized landing page for a lead."""

    lead = get_object_or_404(Lead.objects.prefetch_related("concepts", "dishes"), slug=slug)
    concepts = lead.concepts.all()
    dishes = lead.dishes.all()
    context = {
        "lead": lead,
        "concepts": concepts,
        "dishes": dishes,
    }
    return render(request, "leads/landing.html", context)


def track_open(request: HttpRequest, slug: str) -> HttpResponse:
    """Track an email open before redirecting to the landing page."""

    lead = get_object_or_404(Lead, slug=slug)
    if not lead.opened:
        lead.opened = True
        lead.save(update_fields=["opened"])
    return redirect("lead-landing", slug=lead.slug)
