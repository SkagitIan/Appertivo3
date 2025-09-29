"""Custom Django admin site for the Appertivo dashboard."""

from collections import OrderedDict
from typing import Dict, Iterable, List, Tuple

from django.contrib.admin import AdminSite
from django.contrib.admin.utils import quote
from django.http import HttpRequest
from django.template.response import TemplateResponse
from django.urls import path, reverse


NavItem = Dict[str, str]


class AppertivoAdminSite(AdminSite):
    """Branded admin site with grouped navigation and global search."""

    site_header = "Appertivo Control Center"
    site_title = "Appertivo Admin"
    index_title = "Operations overview"

    #: Pre-defined navigation groups mapped to ``"app_label.ModelName"`` identifiers.
    NAV_GROUPS = OrderedDict(
        {
            "Restaurant Data": [
                "app.Restaurant",
                "app.RestaurantSettings",
                "app.MenuVersion",
                "app.MenuCollection",
                "app.MenuItem",
                "app.Ingredient",
                "app.DishIdea",
                "app.DishIdeaIngredient",
                "app.Enhancement",
                "app.Feedback",
                "app.FeedbackAction",
                "app.OutscraperPayload",
                "app.CollaborationLink",
            ],
            "Content & Ideas": [
                "app.IdeationRun",
                "app.Concept",
                "app.FavoriteConcept",
                "app.FavoriteDish",
                "app.Asset",
                "app.TagDictionary",
                "app.Notification",
            ],
            "System": [
                "app.Account",
                "app.UserProfile",
                "app.Membership",
                "auth.User",
                "auth.Group",
                "app.Plan",
                "app.Subscription",
                "app.EntitlementCounter",
                "app.Job",
                "app.UiEvent",
                "app.NotificationPref",
            ],
        }
    )

    #: Extra links that live alongside the grouped model navigation.
    CUSTOM_LINKS = (
        {
            "group": "System",
            "name": "Manual QA Checklist",
            "url_name": "admin:qa-checklist",
        },
    )

    BRAND_COLORS = {
        "primary": "#B993D6",
        "accent": "#f08000",
        "secondary": "#58B09C",
        "dark": "#49475B",
        "background": "#14080E",
    }

    def each_context(self, request: HttpRequest) -> Dict[str, object]:
        """Inject navigation groupings and branding colors into every view."""

        context = super().each_context(request)
        available_apps: List[Dict[str, object]] = context.get("available_apps", [])
        available_lookup: Dict[Tuple[str, str], Dict[str, object]] = {}

        for app_dict in available_apps:
            app_label = app_dict.get("app_label")
            for model in app_dict.get("models", []):
                key = (app_label, model.get("object_name"))
                available_lookup[key] = {"app": app_dict, "model": model}

        grouped_navigation: List[Dict[str, object]] = []
        used_models: set[Tuple[str, str]] = set()

        for group_title, identifiers in self.NAV_GROUPS.items():
            items: List[NavItem] = []
            for identifier in identifiers:
                app_label, model_name = identifier.split(".")
                lookup_key = (app_label, model_name)
                match = available_lookup.get(lookup_key)
                if not match:
                    continue
                model_dict = match["model"]
                items.append(
                    {
                        "name": model_dict.get("name"),
                        "url": model_dict.get("admin_url"),
                        "add_url": model_dict.get("add_url"),
                    }
                )
                used_models.add(lookup_key)

            for link in self._links_for_group(group_title):
                items.append(link)

            if items:
                grouped_navigation.append({"title": group_title, "items": items})

        leftovers = self._collect_unassigned_models(available_lookup, used_models)
        if leftovers:
            grouped_navigation.append({"title": "Other", "items": leftovers})

        context["admin_nav_groups"] = grouped_navigation
        context["admin_brand_colors"] = self.BRAND_COLORS
        return context

    def _collect_unassigned_models(
        self,
        available_lookup: Dict[Tuple[str, str], Dict[str, object]],
        used_models: Iterable[Tuple[str, str]],
    ) -> List[NavItem]:
        """Return a list of models that were not mapped to a group."""

        leftovers: List[NavItem] = []
        used = set(used_models)
        for (app_label, model_name), match in available_lookup.items():
            if (app_label, model_name) in used:
                continue
            model_dict = match["model"]
            leftovers.append(
                {
                    "name": model_dict.get("name"),
                    "url": model_dict.get("admin_url"),
                    "add_url": model_dict.get("add_url"),
                }
            )
        return leftovers

    def _links_for_group(self, group_title: str) -> List[NavItem]:
        """Return configured custom links for the requested group."""

        links: List[NavItem] = []
        for link in self.CUSTOM_LINKS:
            if link.get("group") != group_title:
                continue
            try:
                url = reverse(link["url_name"])
            except Exception:  # pragma: no cover - guard against stale links
                continue
            links.append({"name": link.get("name"), "url": url, "custom": "true"})
        return links

    def get_urls(self):
        urls = super().get_urls()
        from .admin_views import manual_testing_checklist_view

        custom_urls = [
            path("search/", self.admin_view(self.global_search_view), name="global-search"),
            path(
                "qa-checklist/",
                self.admin_view(manual_testing_checklist_view),
                name="qa-checklist",
            ),
        ]
        return custom_urls + urls

    def global_search_view(self, request: HttpRequest) -> TemplateResponse:
        """Search across all registered models that expose search fields."""

        query = (request.GET.get("q") or "").strip()
        results: List[Dict[str, object]] = []

        if query:
            for model, model_admin in self._registry.items():
                if not model_admin.get_search_fields(request):
                    continue
                if not model_admin.has_view_or_change_permission(request):
                    continue

                queryset = model_admin.get_queryset(request)
                queryset, use_distinct = model_admin.get_search_results(request, queryset, query)

                if use_distinct:
                    queryset = queryset.distinct()

                matches = list(queryset[:5])
                if not matches:
                    continue

                opts = model._meta
                results.append(
                    {
                        "model": model,
                        "opts": opts,
                        "items": [
                            {
                                "object": instance,
                                "change_url": reverse(
                                    f"admin:{opts.app_label}_{opts.model_name}_change",
                                    args=(quote(instance.pk),),
                                ),
                            }
                            for instance in matches
                        ],
                    }
                )

        context = {
            **self.each_context(request),
            "title": f"Search results for “{query}”" if query else "Search",
            "query": query,
            "search_results": results,
        }
        return TemplateResponse(request, "admin/global_search.html", context)


appertivo_admin_site = AppertivoAdminSite(name="appertivo_admin")

