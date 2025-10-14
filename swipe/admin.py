from django.contrib import admin

from .models import Concept, Dish, Favorite


@admin.register(Concept)
class ConceptAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "subtitle", "created_at")
    search_fields = ("name", "subtitle")


@admin.register(Dish)
class DishAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "concept")
    search_fields = ("name", "concept__name")


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "concept", "dish", "created_at")
    search_fields = ("user__username", "concept__name", "dish__name")

