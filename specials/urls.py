from django.contrib import admin
from django.urls import path

from app import views as app_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/signup/", app_views.signup, name="api-signup"),
]
