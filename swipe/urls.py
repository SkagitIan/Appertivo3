from django.urls import path

from .views import *

app_name = "swipe"

urlpatterns = [
    path("", SwipeHomeView.as_view(), name="home"),
    path("favorites/", FavoritesView.as_view(), name="favorites"),
    path("generate-concepts/<uuid:restaurant_id>/", generate_concepts_view, name="generate_concepts"),
    path(
        "api/concepts/<int:concept_id>/dishes/",
        ConceptDishAppendView.as_view(),
        name="concept_append_dishes",
    ),
    path("api/seen/", MarkSeenAPI.as_view(), name="mark_seen"),
    path("api/favorite/", ToggleFavoriteAPI.as_view(), name="api_favorite"),
    path("api/delete/", DeleteCardAPI.as_view(), name="delete_card"),
    path("health/", HealthView.as_view(), name="health"),
]

