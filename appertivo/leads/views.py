"""Views for the leads landing experiences."""
from __future__ import annotations

import json
import logging
import os

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from dotenv import load_dotenv

from .models import Lead, LeadRun
from .tasks import (
    build_lead_run_pipeline,
    dispatch_lead_pipeline,
    extract_lead_entries,
    resolve_outscraper_payload,
    send_personalized_email,
    store_lead_entries,
)
from .utils import extract_outscraper_job_id

load_dotenv()

logger = logging.getLogger(__name__)

TOP_CULINARY_CITIES: list[str] = [
    "New York, NY",
    "Los Angeles, CA",
    "Chicago, IL",
    "San Francisco, CA",
    "Houston, TX",
    "Austin, TX",
    "Portland, OR",
    "Seattle, WA",
    "New Orleans, LA",
    "Nashville, TN",
    "Miami, FL",
    "Charleston, SC",
    "Atlanta, GA",
    "Boston, MA",
    "Philadelphia, PA",
    "Denver, CO",
    "Washington, DC",
    "Las Vegas, NV",
    "San Diego, CA",
    "San Antonio, TX",
    "Minneapolis, MN",
    "Phoenix, AZ",
    "Dallas, TX",
    "Detroit, MI",
    "Kansas City, MO",
]


@csrf_exempt
@require_POST
def outscraper_webhook(request: HttpRequest) -> JsonResponse:
    """Persist Outscraper webhook payloads onto matching lead records."""

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    api_key = os.getenv("OUTSCRAPER_API_KEY")
    headers = {"X-API-KEY": api_key} if api_key else None
    resolved_payload = resolve_outscraper_payload(payload, headers)
    entries = extract_lead_entries(resolved_payload)
    job_id = extract_outscraper_job_id(payload) or extract_outscraper_job_id(resolved_payload)
    run = None
    if job_id:
        run = LeadRun.objects.filter(outscraper_job_id=job_id).first()

    if not entries:
        logger.info("Received Outscraper webhook with no lead entries")
        if run is not None:
            run.status = LeadRun.Status.READY
            run.processed_leads = run.total_leads
            run.save(update_fields=["status", "processed_leads"])
        return JsonResponse({"status": "ok", "processed": 0})

    existing_ids: set[int] = set()
    if run is not None:
        existing_ids = set(run.leads.values_list("id", flat=True))

    lead_ids = store_lead_entries(entries, city=run.city if run else None, run=run)
    if run is not None:
        new_ids = [lead_id for lead_id in lead_ids if lead_id not in existing_ids]
        run.total_leads = run.leads.count()
        if run.total_leads and run.total_leads > run.expected_leads:
            run.expected_leads = run.total_leads
        run.save(update_fields=["total_leads", "expected_leads"])
        if new_ids:
            dispatch_lead_pipeline.delay(new_ids, run_id=run.pk, send_email=False)
        else:
            run.status = LeadRun.Status.READY
            run.processed_leads = run.total_leads
            run.save(update_fields=["status", "processed_leads"])

    logger.info("Processed %s leads from Outscraper webhook", len(lead_ids))
    return JsonResponse({"status": "ok", "processed": len(lead_ids)})


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

    runs_qs = LeadRun.objects.prefetch_related("leads").order_by("-created_at")
    runs = list(runs_qs)
    pending_runs: list[LeadRun] = []
    run_cards: list[dict[str, object]] = []
    ready_runs = 0
    total_leads = 0
    for run in runs:
        leads = list(run.leads.all())
        total_leads += len(leads)
        if run.status == LeadRun.Status.READY and leads:
            ready_runs += 1
        if not leads:
            pending_runs.append(run)
            continue

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
        "pending_runs": pending_runs,
        "LeadRunStatus": LeadRun.Status,
        "default_limit": 10,
        "city_choices": TOP_CULINARY_CITIES,
        "dashboard_metrics": {
            "total_runs": len(runs),
            "ready_runs": ready_runs,
            "pending_runs": len(pending_runs),
            "total_leads": total_leads,
        },
    }
    return render(request, "leads/dashboard.html", context)


@login_required
@require_POST
def start_lead_run(request: HttpRequest) -> HttpResponse:
    """Create a new lead run and trigger the asynchronous pipeline."""

    selection = (request.POST.get("city_choice") or "").strip()
    custom_city = (request.POST.get("custom_city") or "").strip()
    if selection and selection != "__custom__":
        raw_city = selection
    elif selection == "__custom__":
        raw_city = custom_city
    else:
        legacy_city = (request.POST.get("city") or "").strip()
        raw_city = custom_city or legacy_city
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
def delete_run(request: HttpRequest, run_id: int) -> HttpResponse:
    """Delete a lead run and its associated leads."""

    run = get_object_or_404(LeadRun, pk=run_id)
    run.delete()
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
