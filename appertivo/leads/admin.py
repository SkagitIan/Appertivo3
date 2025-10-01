"""Admin configuration for the leads app."""
from __future__ import annotations

from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.utils.html import format_html

from . import tasks
from .models import Concept, DishIdea, EmailTemplate, Lead


class LeadAdmin(admin.ModelAdmin):
    """Admin interface for managing leads."""

    list_display = (
        "name",
        "email",
        "city",
        "view_demo_button",
        "emailed",
        "opened",
        "email_bounced",
        "followed_up",
        "converted",
    )
    list_filter = ("emailed", "opened", "email_bounced", "followed_up", "converted")
    search_fields = ("name", "email", "city")
    actions = ("contact_selected_leads", "mark_as_followed_up")

    @admin.display(description="View Demo")
    def view_demo_button(self, obj: Lead) -> str:
        if not obj.landing_url:
            return "—"
        return format_html(
            '<a class="button" href="{}" target="_blank" rel="noopener">View Demo</a>',
            obj.landing_url,
        )

    @admin.action(description="Contact selected leads")
    def contact_selected_leads(self, request, queryset):
        for lead in queryset:
            tasks.send_personalized_email.delay(lead.id)
        self.message_user(request, f"Scheduled outreach for {queryset.count()} leads.")

    @admin.action(description="Mark as Followed Up")
    def mark_as_followed_up(self, request, queryset):
        updated = queryset.update(followed_up=True)
        self.message_user(request, f"Marked {updated} leads as followed up.")


class ConceptAdmin(admin.ModelAdmin):
    """Admin configuration for concepts."""

    list_display = ("name", "lead", "rank_order", "enhanced")
    list_filter = ("enhanced",)
    search_fields = ("name", "lead__name")


class DishIdeaAdmin(admin.ModelAdmin):
    """Admin configuration for dish ideas."""

    list_display = ("title", "lead", "favorited", "concept")
    list_filter = ("favorited",)
    search_fields = ("title", "lead__name")


class EmailTemplateAdmin(admin.ModelAdmin):
    """Admin configuration for outreach templates."""

    list_display = ("name", "active", "updated_at")
    list_filter = ("active",)
    search_fields = ("name", "subject")


def _safe_register(model, admin_class) -> None:
    """Register ``admin_class`` for ``model`` without raising on reload."""

    try:
        admin.site.unregister(model)
    except NotRegistered:
        pass
    admin.site.register(model, admin_class)


_safe_register(Lead, LeadAdmin)
_safe_register(Concept, ConceptAdmin)
_safe_register(DishIdea, DishIdeaAdmin)
_safe_register(EmailTemplate, EmailTemplateAdmin)
