"""URL patterns for the internal dashboard."""

from django.urls import path

from .views import DashboardView, LogFeedView

app_name = "dashboard"

urlpatterns = [
    path("", DashboardView.as_view(), name="overview"),
    path("logs/", LogFeedView.as_view(), name="logs"),
]
