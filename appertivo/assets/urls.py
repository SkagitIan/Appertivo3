"""URL patterns for the internal assets workspace."""

from django.urls import path

from . import views

app_name = "assets"

urlpatterns = [
    path("assets/", views.dashboard, name="dashboard"),
    path("assets/preview-jobs/<int:job_id>/", views.preview_status, name="preview-status"),
    path("assets/previews/discard/", views.discard_preview, name="discard-preview"),
    path("assets/library/", views.gallery, name="gallery"),
]
