"""Views for the leads landing experiences."""
from __future__ import annotations

import json
import logging
import os

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from dotenv import load_dotenv

from .models import EmailTemplate, Lead, LeadRun
from .tasks import (
    build_lead_run_pipeline,
    dispatch_lead_pipeline,
    extract_lead_entries,
    generate_concepts_and_dishes,
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
    """Render the consolidated lead management workspace."""

    leads = Lead.objects.select_related("run").order_by("-created_at")
    lead_metrics = {
        "total": Lead.objects.count(),
        "emailed": Lead.objects.filter(emailed=True).count(),
        "opened": Lead.objects.filter(opened=True).count(),
        "bounced": Lead.objects.filter(email_bounced=True).count(),
    }
    run_metrics = {
        "total_runs": LeadRun.objects.count(),
        "in_progress": LeadRun.objects.filter(
            status__in=[LeadRun.Status.FETCHING, LeadRun.Status.PREPARING]
        ).count(),
        "ready": LeadRun.objects.filter(status=LeadRun.Status.READY).count(),
        "completed": LeadRun.objects.filter(status=LeadRun.Status.COMPLETED).count(),
    }

    context = {
        "default_limit": 10,
        "city_choices": TOP_CULINARY_CITIES,
        "leads": leads,
        "lead_metrics": lead_metrics,
        "run_metrics": run_metrics,
        "email_templates": EmailTemplate.objects.filter(active=True).order_by("name"),
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


def _refresh_run_counters(run_ids: set[int]) -> None:
    """Update cached selection counters for the provided runs."""

    for run_id in run_ids:
        try:
            run = LeadRun.objects.get(pk=run_id)
        except LeadRun.DoesNotExist:
            continue
        selected = run.leads.filter(shortlisted=True).count()
        if run.selected_leads != selected:
            run.selected_leads = selected
            run.save(update_fields=["selected_leads"])


@login_required
@require_POST
def process_lead_actions(request: HttpRequest) -> HttpResponse:
    """Handle approval, email, and status actions for one or more leads."""

    action = (request.POST.get("action") or "").strip()
    run_ids: set[int] = set()
    lead_ids: list[int] = []
    for raw in request.POST.getlist("lead_ids"):
        try:
            lead_ids.append(int(raw))
        except (TypeError, ValueError):
            continue

    if not lead_ids:
        messages.error(request, "Select at least one lead to continue.")
        return redirect("lead-dashboard")

    leads = list(Lead.objects.select_related("run").filter(id__in=lead_ids))
    if not leads:
        messages.error(request, "No matching leads were found.")
        return redirect("lead-dashboard")

    template_pk: int | None = None
    raw_template = request.POST.get("template_id")
    if raw_template:
        try:
            template_pk = int(raw_template)
        except (TypeError, ValueError):
            template_pk = None

    if action == "approve":
        for lead in leads:
            updates: list[str] = []
            if not lead.shortlisted:
                lead.shortlisted = True
                updates.append("shortlisted")
            if lead.email_bounced:
                lead.email_bounced = False
                updates.append("email_bounced")
            if updates:
                lead.save(update_fields=updates)

            signature = generate_concepts_and_dishes.s(lead.id)
            if template_pk is not None:
                signature = signature | send_personalized_email.s(template_pk)
            else:
                signature = signature | send_personalized_email.s()
            signature.delay()

            if lead.run_id:
                run_ids.add(lead.run_id)
        messages.success(request, f"Scheduled approval workflow for {len(leads)} lead(s).")
    elif action == "mark_bounced":
        for lead in leads:
            if not lead.email_bounced:
                lead.email_bounced = True
                lead.save(update_fields=["email_bounced"])
        messages.info(request, "Marked emails as bounced.")
    elif action == "clear_bounce":
        for lead in leads:
            if lead.email_bounced:
                lead.email_bounced = False
                lead.save(update_fields=["email_bounced"])
        messages.info(request, "Cleared bounce status.")
    else:
        messages.error(request, "Unknown action requested.")

    if run_ids:
        _refresh_run_counters(run_ids)

    return redirect("lead-dashboard")
