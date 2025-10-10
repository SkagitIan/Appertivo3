"""Lookbook-specific view helpers and endpoints.

This module mirrors the concept and dish workflows exposed in
``app.views`` while preparing data for the animated lookbook template.
It keeps the original views untouched, but offers a lightweight facade
that can be wired to the new UI without rewriting the existing system.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, List, Optional

from django.contrib.auth.decorators import login_required
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Exists, OuterRef, Prefetch
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from . import models

# The lookbook surfaces a small slice of concepts and dishes at a time.
LOOKBOOK_MAX_CONCEPTS = 9
LOOKBOOK_MAX_DISHES = 9

# Duplicate the default placeholders so the lookbook can operate without
# depending on the original module for shared constants.
DEFAULT_PROMPT_PLACEHOLDERS = [
    "Try: Fall brunch specials",
    "Try: Quick lunch menu",
    "Try: New dessert twists",
]

# Price formatting helpers copied from the primary views module so we can
# decorate dish enhancements consistently for the lookbook output.
CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


def format_price_display(cents: Optional[int], currency: Optional[str]) -> str:
    """Convert cents + currency into a printable price string."""

    if cents is None:
        return ""

    currency = (currency or "USD").upper()
    symbol = CURRENCY_SYMBOLS.get(currency)
    amount = cents / 100
    if symbol:
        return f"{symbol}{amount:,.2f}"
    return f"{currency} {amount:,.2f}"


def decorate_dishes_with_enhancements(
    dishes: Iterable[models.DishIdea],
) -> List[models.DishIdea]:
    """Attach enhancement metadata to dish objects for rendering."""

    dish_list = list(dishes)
    if not dish_list:
        return dish_list

    enhancements = (
        models.Enhancement.objects.filter(dish__in=dish_list)
        .select_related("image_asset")
        .order_by("dish_id", "-created_at")
    )

    latest_by_dish = {}
    for enhancement in enhancements:
        latest_by_dish.setdefault(enhancement.dish_id, enhancement)

    for dish in dish_list:
        enhancement = latest_by_dish.get(dish.id)
        dish.latest_enhancement = enhancement
        dish.is_enhanced = enhancement is not None
        names = getattr(dish, "ingredient_names", []) or []
        dish.ingredient_overlap = list(names)
        if enhancement and enhancement.image_asset:
            dish.enhancement_image_url = enhancement.image_asset.public_url
        else:
            dish.enhancement_image_url = None
        if enhancement and enhancement.suggested_price_cents is not None:
            dish.enhancement_price_display = format_price_display(
                enhancement.suggested_price_cents,
                enhancement.currency,
            )
        else:
            dish.enhancement_price_display = ""

    return dish_list


def format_run_duration(run: Optional[models.IdeationRun]) -> Optional[str]:
    """Return a short string describing the runtime for a concept run."""

    if not run:
        return None

    started = run.started_at or run.created_at
    finished = run.finished_at or timezone.now()
    if not started or not finished:
        return None

    delta = finished - started
    total_seconds = int(delta.total_seconds())
    if total_seconds < 1:
        return "<1s"

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    parts: List[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds and not hours:
        parts.append(f"{seconds}s")

    if not parts:
        parts.append("<1s")

    return " ".join(parts)


def _get_unfavorited_concept_names(
    restaurant: Optional[models.Restaurant], limit: int
) -> List[str]:
    """Return the most recent unfavorited concept names for a restaurant."""

    if not restaurant:
        return []

    names = list(
        models.Concept.objects.filter(
            restaurant=restaurant, is_unfavorite=True
        )
        .order_by("-created_at")
        .values_list("name", flat=True)[:limit]
    )
    return [name for name in names if name]


def _primary_restaurant_for_user(user) -> Optional[models.Restaurant]:
    """Return the first restaurant associated with the authenticated user."""

    membership = (
        models.Membership.objects.filter(user=user)
        .select_related("account")
        .first()
    )
    if not membership:
        return None
    return (
        models.Restaurant.objects.filter(account=membership.account)
        .order_by("created_at")
        .first()
    )


@dataclass
class SerializedDish:
    """Serialized representation of a dish for the lookbook."""

    id: str
    title: str
    description: str
    ingredient_notes: List[str]
    tags: List[str]
    is_favorited: bool
    favorite_url: str
    variation_url: str
    enhancement_image_url: Optional[str]
    enhancement_price_display: str


@dataclass
class SerializedConcept:
    """Serialized representation of a concept for the lookbook."""

    id: str
    name: str
    subtitle: str
    reasoning: str
    tags: List[str]
    is_favorited: bool
    is_unfavorited: bool
    has_dishes: bool
    runtime_display: Optional[str]
    generated_at: Optional[str]
    sketch_image_url: Optional[str]
    favorite_url: str
    background_url: str
    dishes_generate_url: str
    dish_detail_url: str
    dishes: List[SerializedDish]


def _serialize_dish(
    dish: models.DishIdea,
    favorite_dish_ids: set[str],
) -> SerializedDish:
    """Convert a DishIdea into a serializable payload."""

    category_tags = getattr(dish, "category_tags", []) or []
    if isinstance(category_tags, (tuple, set)):
        category_tags = list(category_tags)

    return SerializedDish(
        id=str(dish.id),
        title=dish.title,
        description=dish.description,
        ingredient_notes=[str(item) for item in getattr(dish, "ingredient_overlap", [])],
        tags=[str(tag) for tag in category_tags if str(tag).strip()],
        is_favorited=str(dish.id) in favorite_dish_ids,
        favorite_url=reverse("dish_favorite", args=[dish.id]),
        variation_url=reverse("dish-variation", args=[dish.id]),
        enhancement_image_url=getattr(dish, "enhancement_image_url", None),
        enhancement_price_display=getattr(dish, "enhancement_price_display", ""),
    )


def _serialize_concept(
    concept: models.Concept,
    favorite_dish_ids: set[str],
) -> SerializedConcept:
    """Serialize a concept and its latest dishes for the lookbook."""

    dishes = list(getattr(concept, "_lookbook_dishes", [])[:LOOKBOOK_MAX_DISHES])
    decorate_dishes_with_enhancements(dishes)
    serialized_dishes = [_serialize_dish(dish, favorite_dish_ids) for dish in dishes]

    tags = concept.tags or []
    if isinstance(tags, (tuple, set)):
        tags = list(tags)

    favorites = getattr(concept, "_favorites_for_request_user", [])
    runtime_display = format_run_duration(getattr(concept, "ideation_run", None))
    generated_at = None
    if getattr(concept, "ideation_run", None):
        finished_at = concept.ideation_run.finished_at or concept.ideation_run.created_at
        if finished_at:
            generated_at = finished_at.isoformat()
    elif concept.created_at:
        generated_at = concept.created_at.isoformat()

    return SerializedConcept(
        id=str(concept.id),
        name=concept.name,
        subtitle=concept.subtitle,
        reasoning=concept.reasoning,
        tags=[str(tag) for tag in tags if str(tag).strip()],
        is_favorited=bool(favorites),
        is_unfavorited=concept.is_unfavorite,
        has_dishes=concept.has_dishes or bool(serialized_dishes),
        runtime_display=runtime_display,
        generated_at=generated_at,
        sketch_image_url=concept.sketch_image_url,
        favorite_url=reverse("concept-favorite", args=[concept.id]),
        background_url=reverse("concept-background", args=[concept.id]),
        dishes_generate_url=reverse("dishes-generate", args=[concept.id]),
        dish_detail_url=reverse("dish_detail", args=[concept.id]),
        dishes=serialized_dishes,
    )


def _build_concept_queryset(user) -> Iterable[models.Concept]:
    """Return a queryset with favorites and dishes prefetched."""

    concepts_qs = models.Concept.objects.order_by("-created_at").annotate(
        has_dishes=Exists(
            models.DishIdea.objects.filter(
                parent_concept=OuterRef("pk"), is_deleted=False
            )
        )
    )

    if getattr(user, "is_authenticated", False):
        concepts_qs = concepts_qs.prefetch_related(
            Prefetch(
                "favoriteconcept_set",
                queryset=models.FavoriteConcept.objects.filter(user=user),
                to_attr="_favorites_for_request_user",
            )
        )

    concepts_qs = concepts_qs.prefetch_related(
        Prefetch(
            "dishidea_set",
            queryset=models.DishIdea.objects.filter(is_deleted=False)
            .select_related("parent_concept", "restaurant", "ideation_run")
            .order_by("-created_at"),
            to_attr="_lookbook_dishes",
        )
    )
    return concepts_qs


def _collect_favorite_dish_ids(
    user,
    dishes: Iterable[models.DishIdea],
) -> set[str]:
    """Return favorite dish ids for the provided dish iterable."""

    dish_ids = [dish.id for dish in dishes]
    if not dish_ids or not getattr(user, "is_authenticated", False):
        return set()

    favorites = models.FavoriteDish.objects.filter(
        user=user, dish_id__in=dish_ids
    ).values_list("dish_id", flat=True)
    return {str(dish_id) for dish_id in favorites}


def _build_lookbook_payload(request: HttpRequest) -> dict:
    """Assemble serialized concepts and related metadata for the template."""

    restaurant = _primary_restaurant_for_user(request.user)
    concepts = list(_build_concept_queryset(request.user)[:LOOKBOOK_MAX_CONCEPTS])

    all_dishes: List[models.DishIdea] = []
    for concept in concepts:
        all_dishes.extend(getattr(concept, "_lookbook_dishes", [])[:LOOKBOOK_MAX_DISHES])

    favorite_dish_ids = _collect_favorite_dish_ids(request.user, all_dishes)
    serialized_concepts = [
        _serialize_concept(concept, favorite_dish_ids) for concept in concepts
    ]

    payload = {
        "concepts": [asdict(concept) for concept in serialized_concepts],
        "disliked_concepts": _get_unfavorited_concept_names(restaurant, 6),
        "endpoints": {
            "concept_generate": reverse("concepts-generate"),
            "favorites_overview": reverse("favorites"),
        },
        "prompt_placeholders": DEFAULT_PROMPT_PLACEHOLDERS,
    }
    return payload


@login_required
def lookbook_view(request: HttpRequest) -> HttpResponse:
    """Render the animated lookbook template with serialized data."""

    payload = _build_lookbook_payload(request)
    return render(
        request,
        "_partials/lookbook.html",
        {"lookbook_payload": payload},
    )


@login_required
def lookbook_data_view(request: HttpRequest) -> JsonResponse:
    """Return the lookbook payload as JSON for asynchronous consumers."""

    payload = _build_lookbook_payload(request)
    return JsonResponse(payload, encoder=DjangoJSONEncoder, safe=False)


@login_required
def lookbook_concepts_generate_view(request: HttpRequest) -> HttpResponse:
    """Proxy to the existing concept generation workflow."""

    from .views import concepts_generate_view

    return concepts_generate_view(request)


@login_required
def lookbook_concept_favorite_view(request: HttpRequest, concept_id) -> HttpResponse:
    """Proxy to the concept favorite toggle for lookbook routes."""

    from .views import concept_favorite_view

    return concept_favorite_view(request, concept_id)


@login_required
def lookbook_concept_background_view(request: HttpRequest, concept_id) -> HttpResponse:
    """Proxy to the concept sketch generation endpoint."""

    from .views import concept_background_view

    return concept_background_view(request, concept_id)


@login_required
def lookbook_dishes_generate_view(request: HttpRequest, concept_id) -> HttpResponse:
    """Proxy to the dish generation workflow for lookbook routes."""

    from .views import dishes_generate_view

    return dishes_generate_view(request, concept_id)


@login_required
def lookbook_dish_detail_view(request: HttpRequest, concept_id) -> HttpResponse:
    """Proxy to the dish detail view so lookbook routes can reuse it."""

    from .views import dish_detail_view

    return dish_detail_view(request, concept_id)


def lookbook_dish_favorite_view(request: HttpRequest, dish_id) -> HttpResponse:
    """Proxy to the dish favorite toggle (authentication handled upstream)."""

    from .views import dish_favorite_view

    return dish_favorite_view(request, dish_id)
