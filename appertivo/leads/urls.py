"""URL configuration for the leads app."""
from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("demo/<slug:slug>/", views.lead_landing, name="lead-landing"),
    path("demo/<slug:slug>/track/", views.track_open, name="lead-track"),
]
