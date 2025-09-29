
"""Admin registrations for the custom Appertivo admin site."""

from typing import List

from django.contrib import admin
from django.contrib.admin import ModelAdmin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group
from django.db import models as django_models
from django.core.exceptions import FieldDoesNotExist

from . import models
from .admin_site import appertivo_admin_site


def _model_has_field(model: type[django_models.Model], field_name: str) -> bool:
    """Return ``True`` if ``field_name`` is a concrete model field."""

    try:
        model._meta.get_field(field_name)
    except FieldDoesNotExist:
        return False
    return True


class TimestampedAdmin(ModelAdmin):
    """Base admin that favours human-readable list columns."""

    readonly_fields = ("id", "created_at")
    list_display: tuple[str, ...] = ()
    ordering = ("-created_at",)

    def display_label(self, obj):
        """Return a descriptive label for list displays."""

        for attr in ("name", "title", "code", "slug"):
            if hasattr(obj, attr):
                value = getattr(obj, attr)
                if value:
                    return value
        return str(obj)

    display_label.short_description = "Name"

    def get_list_display(self, request):
        display = list(super().get_list_display(request))
        if not display:
            display = ["display_label"]
        if "display_label" not in display:
            display.insert(0, "display_label")
        if _model_has_field(self.model, "status") and "status" not in display:
            display.append("status")
        if _model_has_field(self.model, "created_at") and "created_at" not in display:
            display.append("created_at")
        return display

    def get_list_display_links(self, request, list_display):
        links = super().get_list_display_links(request, list_display)
        if links is None and "display_label" in list_display:
            return ("display_label",)
        return links

    def get_search_fields(self, request):
        base = list(super().get_search_fields(request))
        for field_name in ("name", "title", "description", "code", "slug"):
            if _model_has_field(self.model, field_name):
                base.append(field_name)
        return tuple(dict.fromkeys(base))

    def get_list_filter(self, request):
        filters: List = list(super().get_list_filter(request))
        if _model_has_field(self.model, "status") and "status" not in filters:
            filters.append("status")
        return tuple(filters)


# Register core org/account/user models
@admin.register(models.Account, site=appertivo_admin_site)
class AccountAdmin(TimestampedAdmin):
    list_display = ("name", "stripe_customer_id", "created_at")


@admin.register(models.UserProfile, site=appertivo_admin_site)
class UserProfileAdmin(TimestampedAdmin):
    list_display = ("user", "timezone", "created_at")


@admin.register(models.Membership, site=appertivo_admin_site)
class MembershipAdmin(TimestampedAdmin):
    list_display = ("account", "user", "role", "created_at")
    list_filter = ("role", "account")


# Restaurant + data
class MenuVersionInline(admin.TabularInline):
    model = models.MenuVersion
    extra = 0
    fields = ("source_kind", "status", "parsed_at")
    readonly_fields = ("parsed_at",)


class DishIdeaInline(admin.TabularInline):
    model = models.DishIdea
    extra = 0
    fields = ("title", "description")
    show_change_link = True


@admin.register(models.Onboarding, site=appertivo_admin_site)
class OnboardingAdmin(TimestampedAdmin):
    list_display = (
        "user",
        "restaurant",
        "state",
        "progress",
        "updated_at",
    )
    list_filter = ("state",)
    readonly_fields = (
        "outscraper_search_job_id",
        "outscraper_reviews_job_id",
        "web_profile_json",
        "reviews_json",
        "review_analysis_json",
        "personas_json",
        "last_error",
    )


@admin.register(models.OnboardingEvent, site=appertivo_admin_site)
class OnboardingEventAdmin(TimestampedAdmin):
    list_display = ("onboarding", "from_state", "to_state", "created_at")
    list_filter = ("to_state",)


@admin.register(models.Restaurant, site=appertivo_admin_site)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = ("name", "location_text", "phone", "rating", "review_count", "created_at")
    search_fields = ("name", "location_text", "phone")
    list_filter = ("rating", "account")
    readonly_fields = ("context_json", "reviews_json")
    inlines = (MenuVersionInline, DishIdeaInline)

    fieldsets = (
        ("Core Info", {
            "fields": (
                "account",
                "name",
                "location_text",
                "primary_menu_url",
                "menu_urls",
            )
        }),
        ("Outscraper Data", {
            "fields": (
                "phone", "website", "google_place_id",
                "description", "rating", "review_count",
                "hours_json", "about_json", "context_json", "reviews_json"
            )
        }),
        ("Menu", {
            "fields": ("active_menu_version",)
        }),
    )

@admin.register(models.RestaurantSettings, site=appertivo_admin_site)
class RestaurantSettingsAdmin(TimestampedAdmin):
    list_display = (
        "restaurant",
        "classic_creative_slider",
        "default_currency",
        "updated_at",
    )


@admin.register(models.OutscraperPayload, site=appertivo_admin_site)
class OutscraperPayloadAdmin(TimestampedAdmin):
    list_display = ("restaurant", "status", "started_at", "finished_at")
    list_filter = ("status", "restaurant")


@admin.register(models.MenuVersion, site=appertivo_admin_site)
class MenuVersionAdmin(TimestampedAdmin):
    list_display = ("restaurant", "source_kind", "status", "parsed_at")
    list_filter = ("status", "source_kind", "restaurant")


@admin.register(models.Ingredient, site=appertivo_admin_site)
class IngredientAdmin(TimestampedAdmin):
    list_display = ("restaurant", "name", "canonical_name", "confidence")
    search_fields = ("name", "canonical_name")


# Ideation + results
@admin.register(models.IdeationRun, site=appertivo_admin_site)
class IdeationRunAdmin(TimestampedAdmin):
    list_display = ("restaurant", "type", "model_name", "status", "created_at")
    list_filter = ("status", "type", "restaurant")


@admin.register(models.Concept, site=appertivo_admin_site)
class ConceptAdmin(TimestampedAdmin):
    list_display = ("restaurant", "name", "rank_order", "created_at")


@admin.register(models.DishIdea, site=appertivo_admin_site)
class DishIdeaAdmin(TimestampedAdmin):
    list_display = ("restaurant", "title", "description", "created_at")
    search_fields = ("title", "description")


@admin.register(models.DishIdeaIngredient, site=appertivo_admin_site)
class DishIdeaIngredientAdmin(TimestampedAdmin):
    list_display = ("dish", "ingredient", "source", "confidence")
    list_filter = ("source",)


# Favorites
@admin.register(models.FavoriteConcept, site=appertivo_admin_site)
class FavoriteConceptAdmin(TimestampedAdmin):
    list_display = ("user", "concept", "favorited_at")


@admin.register(models.FavoriteDish, site=appertivo_admin_site)
class FavoriteDishAdmin(TimestampedAdmin):
    list_display = ("user", "dish", "favorited_at")


# Assets + enhancements
@admin.register(models.Asset, site=appertivo_admin_site)
class AssetAdmin(TimestampedAdmin):
    list_display = ("kind", "public_url", "created_at")
    search_fields = ("public_url", "kind")


@admin.register(models.Enhancement, site=appertivo_admin_site)
class EnhancementAdmin(TimestampedAdmin):
    list_display = (
        "dish",
        "status",
        "suggested_price_cents",
        "currency",
        "created_at",
    )
    list_filter = ("status",)


# Menus
@admin.register(models.MenuCollection, site=appertivo_admin_site)
class MenuCollectionAdmin(TimestampedAdmin):
    list_display = ("restaurant", "name", "created_by_user", "created_at")


@admin.register(models.MenuItem, site=appertivo_admin_site)
class MenuItemAdmin(TimestampedAdmin):
    list_display = ("menu", "dish", "position")
    list_filter = ("menu",)


@admin.register(models.CollaborationLink, site=appertivo_admin_site)
class CollaborationLinkAdmin(TimestampedAdmin):
    list_display = (
        "menu",
        "is_active",
        "expires_at",
        "last_accessed_at",
        "access_count",
    )
    list_filter = ("is_active", "menu__restaurant")


@admin.register(models.Feedback, site=appertivo_admin_site)
class FeedbackAdmin(TimestampedAdmin):
    list_display = ("menu", "dish", "type", "anon_id", "created_at")
    list_filter = ("type", "menu__restaurant")


@admin.register(models.FeedbackAction, site=appertivo_admin_site)
class FeedbackActionAdmin(TimestampedAdmin):
    list_display = ("feedback", "status", "decided_by", "decided_at")
    list_filter = ("status",)


# Notifications
@admin.register(models.NotificationPref, site=appertivo_admin_site)
class NotificationPrefAdmin(TimestampedAdmin):
    list_display = (
        "user",
        "on_background_complete_email",
        "on_new_menu_version_email",
    )


@admin.register(models.Notification, site=appertivo_admin_site)
class NotificationAdmin(TimestampedAdmin):
    list_display = ("user", "type", "channel", "status", "sent_at", "read_at")
    list_filter = ("status", "channel")


# Plans + subscriptions
@admin.register(models.Plan, site=appertivo_admin_site)
class PlanAdmin(TimestampedAdmin):
    list_display = ("code", "name")


@admin.register(models.Subscription, site=appertivo_admin_site)
class SubscriptionAdmin(TimestampedAdmin):
    list_display = (
        "account",
        "plan",
        "status",
        "provider",
        "current_period_end",
    )
    list_filter = ("status", "provider")


@admin.register(models.EntitlementCounter, site=appertivo_admin_site)
class EntitlementCounterAdmin(TimestampedAdmin):
    list_display = (
        "account",
        "period_start",
        "concept_runs",
        "dish_runs",
        "enhancements",
    )


# Jobs, events, tags
@admin.register(models.Job, site=appertivo_admin_site)
class JobAdmin(TimestampedAdmin):
    list_display = ("account", "kind", "status", "progress_pct")
    list_filter = ("status", "kind")


@admin.register(models.UiEvent, site=appertivo_admin_site)
class UiEventAdmin(TimestampedAdmin):
    list_display = ("user", "name", "entity_type", "created_at")
    list_filter = ("entity_type",)


@admin.register(models.TagDictionary, site=appertivo_admin_site)
class TagDictionaryAdmin(TimestampedAdmin):
    list_display = ("kind", "name", "slug")
    list_filter = ("kind",)


# Register auth models on the custom site so they inherit the new styling.
User = get_user_model()
appertivo_admin_site.register(User, UserAdmin)
appertivo_admin_site.register(Group, GroupAdmin)
