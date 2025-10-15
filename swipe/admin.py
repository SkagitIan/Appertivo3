from django.contrib import admin

from .models import Concept, Dish, Favorite


@admin.register(Concept)
class ConceptAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "subtitle", "is_deleted", "created_at")
    search_fields = ("name", "subtitle")
    list_filter = ("is_deleted",)


@admin.register(Dish)
class DishAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "concept", "is_deleted")
    search_fields = ("name", "concept__name")
    list_filter = ("is_deleted",)


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "concept", "dish", "created_at")
    search_fields = ("user__username", "concept__name", "dish__name")

