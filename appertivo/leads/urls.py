"""URL configuration for the leads app."""
from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("leads/", views.lead_dashboard, name="lead-dashboard"),
    path("leads/dashboard-panel/", views.lead_dashboard_panel, name="lead-dashboard-panel"),
    path("leads/outscraper-webhook/", views.outscraper_webhook, name="outscraper_webhook"),
    path("leads/runs/start/", views.start_lead_run, name="lead-run-start"),
    path("leads/runs/<int:run_id>/delete/", views.delete_run, name="lead-run-delete"),
    path("leads/runs/<int:run_id>/", views.update_run_selection, name="lead-run-selection"),
    path("demo/<slug:slug>/", views.lead_landing, name="lead-landing"),
    path("demo/<slug:slug>/track/", views.track_open, name="lead-track"),
]
