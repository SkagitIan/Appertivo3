from django.urls import path

from .views import *

app_name = "swipe"

urlpatterns = [
    path("", SwipeHomeView.as_view(), name="home"),
    path("generate-concepts/<uuid:restaurant_id>/", generate_concepts_view, name="generate_concepts"),
    path("api/seen/", MarkSeenAPI.as_view(), name="mark_seen"),
    # path("api/concepts/", SwipeConceptBatchView.as_view(), name="swipe_concepts"),
    # path("api/concepts/", ConceptBatchAPI.as_view(), name="api_concepts"),
    # path("api/favorite/", ToggleFavoriteAPI.as_view(), name="api_favorite"),
    path("health/", HealthView.as_view(), name="health"),
]

