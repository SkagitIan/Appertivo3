"""URL patterns for the internal assets workspace."""

from django.urls import path

from . import views

app_name = "assets"

urlpatterns = [
    path("assets/", views.dashboard, name="dashboard"),
    path("assets/gallery/", views.gallery, name="gallery"),
]
