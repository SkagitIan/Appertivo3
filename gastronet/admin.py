from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import Restaurant, UrlDiscovery, CrawlLog, MenuAttempt, MenuSnapshot

@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "city",
        "category",
        "rating",
        "review_count",
        "source",
        "last_updated",
        "place_id",
    )
    list_filter = ("city", "category", "source")
    search_fields = ("name", "address", "city", "summary")
    readonly_fields = ("created_at", "last_updated")
    ordering = ("name",)

    fieldsets = (
        ("Identity", {"fields": ("name", "address", "city", "website", "phone","place_id")}),
        ("Classification", {"fields": ("category", "cuisine")}),
        ("Metrics", {"fields": ("rating", "review_count", "sentiment_score")}),
        ("AI Summary", {"fields": ("summary", "keywords")}),
        ("Geo & Embedding", {"fields": ("location", "embedding")}),
        ("Metadata", {"fields": ("source", "created_at", "last_updated")}),
    )


@admin.register(UrlDiscovery)
class UrlDiscoveryAdmin(admin.ModelAdmin):
    list_display = ("query", "result_url", "hit_count", "created_at")
    list_filter = ("created_at",)
    search_fields = ("query", "result_url")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)

    fieldsets = (
        (None, {
            "fields": ("query", "result_url", "hit_count", "created_at")
        }),
    )


@admin.register(CrawlLog)
class CrawlLogAdmin(admin.ModelAdmin):
    list_display = (
        "task",
        "scope",
        "started_at",
        "ended_at",
        "success_count",
        "skip_count",
        "error_count",
        "api_calls",
        "est_cost_usd",
    )
    list_filter = ("task", "scope", "started_at")
    search_fields = ("task", "scope", "notes")
    date_hierarchy = "started_at"
    readonly_fields = (
        "started_at",
        "ended_at",
        "notes",
    )
    ordering = ("-started_at",)

    fieldsets = (
        ("Task Info", {
            "fields": ("task", "scope", "notes")
        }),
        ("Run Stats", {
            "fields": (
                "started_at",
                "ended_at",
                "success_count",
                "skip_count",
                "error_count",
                "api_calls",
                "est_cost_usd",
            )
        }),
    )


@admin.register(MenuSnapshot)
class MenuSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "restaurant",
        "fetched_at",
        "source_url",
        "short_hash",
        "text_len",
    )
    list_filter = ("fetched_at",)
    search_fields = ("restaurant__name", "source_url", "hash")
    readonly_fields = ("fetched_at", "hash", "text_len", "short_hash")
    ordering = ("-fetched_at",)

    def short_hash(self, obj):
        return obj.hash[:10] if obj.hash else ""
    short_hash.short_description = "Hash"

    def text_len(self, obj):
        return len(obj.text or "")
    text_len.short_description = "Chars"

    fieldsets = (
        ("Snapshot Info", {
            "fields": ("restaurant", "fetched_at", "source_url", "hash", "short_hash", "text_len")
        }),
        ("Content", {
            "fields": ("text", "parsed_json"),
        }),
    )

@admin.register(MenuAttempt)
class MenuAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "restaurant",
        "tried_url",
        "source",
        "found",
        "parsed",
        "status",
        "created_at",
        "finished_at",
    )
    list_filter = ("source", "found", "parsed", "created_at")
    search_fields = ("restaurant__name", "tried_url", "status")
    readonly_fields = ("created_at", "finished_at")
    ordering = ("-created_at",)

    fieldsets = (
        ("Attempt Info", {
            "fields": (
                "restaurant",
                "tried_url",
                "source",
                "status",
                "found",
                "parsed",
            )
        }),
        ("Timestamps", {
            "fields": ("created_at", "finished_at"),
        }),
    )

from django.contrib import admin
from .models import MenuItem

@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "restaurant",
        "section",
        "price",
        "currency",
        "scraped_at",
    )
    list_filter = ("restaurant", "section", "currency", "scraped_at")
    search_fields = ("name", "description", "restaurant__name")
    ordering = ("restaurant", "section", "name")
    readonly_fields = ("scraped_at",)
    fieldsets = (
        (None, {
            "fields": (
                "restaurant",
                "name",
                "description",
                "price",
                "currency",
                "section",
                "dietary_tags",
                "source_url",
                "scraped_at",
            )
        }),
    )

    # optional for compact admin layout
    list_per_page = 50
