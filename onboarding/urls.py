"""URL configuration for onboarding endpoints."""

from django.urls import path

from . import views

urlpatterns = [
    path("webhooks/stripe", views.stripe_webhook, name="stripe_webhook"),
    path(
        "api/onboarding/<uuid:onboarding_id>/status",
        views.onboarding_status,
        name="onboarding_status",
    ),
]
