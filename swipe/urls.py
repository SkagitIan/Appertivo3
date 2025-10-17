from django.urls import path

from .views import *

app_name = "swipe"

urlpatterns = [
    path("", SwipeHomeView.as_view(), name="home"),
    path("demo/", SwipeDemoView.as_view(), name="demo_home"),
    path("demo/favorites/", DemoFavoritesView.as_view(), name="demo_favorites"),
    path("demo/settings/", DemoSettingsView.as_view(), name="demo_settings"),
    path("favorites/", FavoritesView.as_view(), name="favorites"),
    path("settings/", SettingsView.as_view(), name="settings"),
    path("generate-concepts/<uuid:restaurant_id>/", generate_concepts_view, name="generate_concepts"),
    path(
        "api/concepts/<int:concept_id>/dishes/",
        ConceptDishAppendView.as_view(),
        name="concept_append_dishes",
    ),
    path(
        "api/dishes/<int:dish_id>/variation/",
        DishVariationView.as_view(),
        name="dish_variation",
    ),
    path("api/seen/", MarkSeenAPI.as_view(), name="mark_seen"),
    path("api/favorite/", ToggleFavoriteAPI.as_view(), name="api_favorite"),
    path("api/delete/", DeleteCardAPI.as_view(), name="delete_card"),
    path("health/", HealthView.as_view(), name="health"),
]

