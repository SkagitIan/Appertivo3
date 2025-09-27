from django.contrib import admin
from . import models


# Simple generic base
class TimestampedAdmin(admin.ModelAdmin):
    readonly_fields = ("id", "created_at")
    list_display = ("id", "created_at")
    ordering = ("-created_at",)


# Register core org/account/user models
@admin.register(models.Account)
class AccountAdmin(TimestampedAdmin):
    list_display = ("id", "name", "stripe_customer_id", "created_at")


@admin.register(models.UserProfile)
class UserProfileAdmin(TimestampedAdmin):
    list_display = ("id", "user", "timezone", "created_at")


@admin.register(models.Membership)
class MembershipAdmin(TimestampedAdmin):
    list_display = ("id", "account", "user", "role", "created_at")


# Restaurant + data
@admin.register(models.Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = ("name", "location_text", "phone", "rating", "review_count")
    search_fields = ("name", "location_text", "phone")
    readonly_fields = ("context_json",)

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
                "hours_json", "about_json", "context_json"
            )
        }),
        ("Menu", {
            "fields": ("active_menu_version",)
        }),
    )

@admin.register(models.RestaurantSettings)
class RestaurantSettingsAdmin(TimestampedAdmin):
    list_display = ("restaurant", "classic_creative_slider", "default_currency", "updated_at")


@admin.register(models.OutscraperPayload)
class OutscraperPayloadAdmin(TimestampedAdmin):
    list_display = ("id", "restaurant", "status", "started_at", "finished_at")


@admin.register(models.MenuVersion)
class MenuVersionAdmin(TimestampedAdmin):
    list_display = ("id", "restaurant", "source_kind", "status", "parsed_at")


@admin.register(models.Ingredient)
class IngredientAdmin(TimestampedAdmin):
    list_display = ("id", "restaurant", "name", "canonical_name", "confidence")


# Ideation + results
@admin.register(models.IdeationRun)
class IdeationRunAdmin(TimestampedAdmin):
    list_display = ("id", "restaurant", "type", "model_name", "status", "created_at")


@admin.register(models.Concept)
class ConceptAdmin(TimestampedAdmin):
    list_display = ("id", "restaurant", "name", "rank_order", "created_at")


@admin.register(models.DishIdea)
class DishIdeaAdmin(TimestampedAdmin):
    list_display = ("id", "restaurant", "title", "description")


@admin.register(models.DishIdeaIngredient)
class DishIdeaIngredientAdmin(TimestampedAdmin):
    list_display = ("id", "dish", "ingredient", "source", "confidence")


# Favorites
@admin.register(models.FavoriteConcept)
class FavoriteConceptAdmin(TimestampedAdmin):
    list_display = ("id", "user", "concept", "favorited_at")


@admin.register(models.FavoriteDish)
class FavoriteDishAdmin(TimestampedAdmin):
    list_display = ("id", "user", "dish", "favorited_at")


# Assets + enhancements
@admin.register(models.Asset)
class AssetAdmin(TimestampedAdmin):
    list_display = ("id", "kind", "public_url", "created_at")


@admin.register(models.Enhancement)
class EnhancementAdmin(TimestampedAdmin):
    list_display = ("id", "dish", "status", "suggested_price_cents", "currency")


# Menus
@admin.register(models.MenuCollection)
class MenuCollectionAdmin(TimestampedAdmin):
    list_display = ("id", "restaurant", "name", "created_by_user")


@admin.register(models.MenuItem)
class MenuItemAdmin(TimestampedAdmin):
    list_display = ("id", "menu", "dish", "position")


@admin.register(models.CollaborationLink)
class CollaborationLinkAdmin(TimestampedAdmin):
    list_display = (
        "id",
        "menu",
        "is_active",
        "expires_at",
        "last_accessed_at",
        "access_count",
    )
    list_filter = ("is_active", "menu__restaurant")


@admin.register(models.Feedback)
class FeedbackAdmin(TimestampedAdmin):
    list_display = ("id", "menu", "dish", "type", "anon_id", "created_at")
    list_filter = ("type", "menu__restaurant")


@admin.register(models.FeedbackAction)
class FeedbackActionAdmin(TimestampedAdmin):
    list_display = ("id", "feedback", "status", "decided_by", "decided_at")
    list_filter = ("status",)


# Notifications
@admin.register(models.NotificationPref)
class NotificationPrefAdmin(TimestampedAdmin):
    list_display = ("id", "user", "on_background_complete_email", "on_new_menu_version_email")


@admin.register(models.Notification)
class NotificationAdmin(TimestampedAdmin):
    list_display = ("id", "user", "type", "channel", "status", "sent_at", "read_at")


# Plans + subscriptions
@admin.register(models.Plan)
class PlanAdmin(TimestampedAdmin):
    list_display = ("id", "code", "name")


@admin.register(models.Subscription)
class SubscriptionAdmin(TimestampedAdmin):
    list_display = ("id", "account", "plan", "status", "provider", "current_period_end")


@admin.register(models.EntitlementCounter)
class EntitlementCounterAdmin(TimestampedAdmin):
    list_display = ("id", "account", "period_start", "concept_runs", "dish_runs", "enhancements")


# Jobs, events, tags
@admin.register(models.Job)
class JobAdmin(TimestampedAdmin):
    list_display = ("id", "account", "kind", "status", "progress_pct")


@admin.register(models.UiEvent)
class UiEventAdmin(TimestampedAdmin):
    list_display = ("id", "user", "name", "entity_type", "created_at")


@admin.register(models.TagDictionary)
class TagDictionaryAdmin(TimestampedAdmin):
    list_display = ("id", "kind", "name", "slug")
