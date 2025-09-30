"""Views for the leads landing experiences."""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import Lead, LeadRun
from .tasks import build_lead_run_pipeline, send_personalized_email

def outscraper_webhook():
    pass

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


@login_required
def lead_dashboard(request: HttpRequest) -> HttpResponse:
    """Render a dashboard for managing Outscraper lead runs."""

    runs = LeadRun.objects.prefetch_related("leads").order_by("-created_at")
    run_cards: list[dict[str, object]] = []
    for run in runs:
        leads = list(run.leads.all())
        leads = sorted(leads, key=lambda lead: lead.created_at, reverse=True)
        leads.sort(key=lambda lead: not lead.shortlisted)
        metrics = {
            "total": len(leads),
            "shortlisted": sum(1 for lead in leads if lead.shortlisted),
            "emailed": sum(1 for lead in leads if lead.emailed),
            "opened": sum(1 for lead in leads if lead.opened),
            "converted": sum(1 for lead in leads if lead.converted),
        }
        lead_entries = []
        for lead in leads:
            fallback_url = request.build_absolute_uri(reverse("lead-landing", args=[lead.slug]))
            landing_url = lead.landing_url or fallback_url
            can_send = bool(lead.email) and run.status in (
                LeadRun.Status.READY,
                LeadRun.Status.COMPLETED,
            )
            lead_entries.append({"instance": lead, "landing_url": landing_url, "can_send": can_send})
        run_cards.append({"run": run, "metrics": metrics, "leads": lead_entries})

    context = {
        "run_cards": run_cards,
        "LeadRunStatus": LeadRun.Status,
        "default_limit": 10,
    }
    return render(request, "leads/dashboard.html", context)


@login_required
@require_POST
def start_lead_run(request: HttpRequest) -> HttpResponse:
    """Create a new lead run and trigger the asynchronous pipeline."""

    raw_city = (request.POST.get("city") or "").strip()
    city = raw_city or None
    raw_limit = request.POST.get("limit")
    try:
        limit = int(raw_limit) if raw_limit else 10
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(50, limit))

    run = LeadRun.objects.create(
        city=city,
        expected_leads=limit,
        status=LeadRun.Status.FETCHING,
    )
    build_lead_run_pipeline(run.id, city=city, limit=limit)
    return redirect("lead-dashboard")


@login_required
@require_POST
def update_run_selection(request: HttpRequest, run_id: int) -> HttpResponse:
    """Persist lead selections and optional email sends for a run."""

    run = get_object_or_404(LeadRun, pk=run_id)

    selected_ids: set[int] = set()
    for raw_id in request.POST.getlist("selected_leads"):
        try:
            selected_ids.add(int(raw_id))
        except (TypeError, ValueError):
            continue

    run.leads.update(shortlisted=False)
    if selected_ids:
        run.leads.filter(id__in=selected_ids).update(shortlisted=True)

    update_fields = ["selected_leads"]
    run.selected_leads = len(selected_ids)

    if request.POST.get("mark_complete"):
        run.status = LeadRun.Status.COMPLETED
        update_fields.append("status")

    run.save(update_fields=update_fields)

    lead_to_email = request.POST.get("send_email")
    if lead_to_email:
        try:
            lead_id = int(lead_to_email)
        except (TypeError, ValueError):
            lead_id = None
        if lead_id:
            try:
                lead = run.leads.get(pk=lead_id)
            except Lead.DoesNotExist:
                lead = None
            if lead is not None:
                send_personalized_email.delay(lead.id)

    return redirect("lead-dashboard")
