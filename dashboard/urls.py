"""URL patterns for the internal dashboard."""

from django.urls import path

from .views import DashboardView

app_name = "dashboard"

urlpatterns = [
    path("", DashboardView.as_view(), name="overview"),
]
