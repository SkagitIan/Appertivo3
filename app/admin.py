
from types import MethodType
from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path
from decimal import Decimal
from django.contrib import admin
from django.db.models import Count, Sum
from django.db.models.functions import Coalesce

from . import models
from .qa_checklist import CHECKLIST_SECTIONS


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


def manual_testing_checklist_view(request):
    """Render the manual QA checklist inside the Django admin."""

    context = admin.site.each_context(request)
    context.update(
        {
            "title": "Manual QA Checklist",
            "checklist_sections": CHECKLIST_SECTIONS,
            "checklist_storage_key": "manual-qa-checklist-v1",
        }
    )
    return TemplateResponse(request, "admin/testing_checklist.html", context)


_original_get_urls = admin.site.get_urls


def _get_urls(self):
    urls = _original_get_urls()
    custom_urls = [
        path(
            "qa-checklist/",
            self.admin_view(manual_testing_checklist_view),
            name="qa-checklist",
        )
    ]
    return custom_urls + urls


admin.site.get_urls = MethodType(_get_urls, admin.site)


@admin.register(models.Notification)
class NotificationAdmin(TimestampedAdmin):
    list_display = ("id", "user", "type", "channel", "status", "sent_at", "read_at")


@admin.register(models.LlmCallLog)
class LlmCallLogAdmin(TimestampedAdmin):
    list_display = (
        "created_at",
        "provider",
        "model_name",
        "call_type",
        "step",
        "user",
        "cost_display",
    )
    list_filter = ("provider", "call_type", "step")
    search_fields = (
        "function_name",
        "model_name",
        "user__email",
        "user__username",
    )

    def cost_display(self, obj: models.LlmCallLog) -> str:
        return obj.cost_display()

    cost_display.short_description = "Cost"

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context=extra_context)
        try:
            queryset = response.context_data["cl"].queryset
        except (KeyError, AttributeError):  # pragma: no cover - admin internals changed
            return response

        totals = queryset.aggregate(
            total_cost=Coalesce(Sum("cost_cents"), 0),
            total_input=Coalesce(Sum("input_tokens"), 0),
            total_output=Coalesce(Sum("output_tokens"), 0),
            total_calls=Coalesce(Count("id"), 0),
        )
        provider_rows = (
            queryset.values("provider")
            .annotate(
                cost=Coalesce(Sum("cost_cents"), 0),
                calls=Count("id"),
            )
            .order_by("-cost")
        )

        response.context_data["cost_summary"] = {
            "total_calls": totals.get("total_calls", 0),
            "total_input": totals.get("total_input", 0),
            "total_output": totals.get("total_output", 0),
            "total_cost": (Decimal(totals.get("total_cost", 0)) / Decimal("100")),
            "per_provider": [
                {
                    "provider": row["provider"] or "unknown",
                    "cost": Decimal(row["cost"]) / Decimal("100"),
                    "calls": row["calls"],
                }
                for row in provider_rows
            ],
        }

        return response


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
