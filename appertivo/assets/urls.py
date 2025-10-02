"""URL patterns for the internal assets workspace."""

from django.urls import path

from . import views

app_name = "assets"

urlpatterns = [
    path("assets/", views.dashboard, name="dashboard"),
    path("assets/models/", views.manage_models, name="manage-models"),
    path("assets/prompts/", views.manage_prompts, name="manage-prompts"),
    path("assets/prompts/enhance/", views.enhance_prompt, name="enhance-prompt"),
    path("assets/preview-jobs/<int:job_id>/", views.preview_status, name="preview-status"),
    path("assets/previews/discard/", views.discard_preview, name="discard-preview"),
    path("assets/folders/<int:folder_id>/verify/", views.verify_folder_pin, name="verify-folder"),
    path("assets/library/", views.gallery, name="gallery"),
]
