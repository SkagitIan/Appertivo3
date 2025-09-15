from django.contrib import admin
from django.urls import path

from app import views as app_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/signup/", app_views.signup, name="api-signup"),
    path("concepts/", app_views.concept_grid, name="concept-grid"),
    path(
        "concepts/<str:concept_name>/dishes/",
        app_views.dish_grid,
        name="dish-grid",
    ),
]
