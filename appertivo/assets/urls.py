"""URL patterns for the internal assets workspace."""

from django.urls import path

from . import views

app_name = "assets"

urlpatterns = [
    path("assets/", views.dashboard, name="dashboard"),
    path("assets/preview-jobs/<int:job_id>/", views.preview_status, name="preview-status"),
    path("assets/library/", views.gallery, name="gallery"),
]
