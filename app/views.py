"""Application views."""

import json, logging, os, uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, List, Optional

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, Max, OuterRef, Prefetch, Q, TextField
from django.db.models.functions import Cast
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST
import stripe
from pydantic import BaseModel
from django.core.cache import cache
import hashlib
from . import models
import base64
import cloudinary.uploader
from openai import OpenAI
from app.llm import _fetch_openai_image, _fetch_gemini_image
from dotenv import load_dotenv
load_dotenv()
_openai_api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=_openai_api_key) if _openai_api_key else None
import datetime
from itertools import islice


from app.tasks import create_ideation_run


stripe.api_key = settings.STRIPE_SECRET_KEY or ""
logger = logging.getLogger(__name__)


DEMO_USER_ID = 17
DEFAULT_CACHE_TIMEOUT = 600
SHORT_CACHE_TIMEOUT = 300


def _stable_hash(value: Any) -> str:
    """Return a stable md5 hash for nested JSON-like data."""

    try:
        serialized = json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        serialized = str(value)
    return hashlib.md5(serialized.encode("utf-8")).hexdigest()

DEFAULT_PROMPT_PLACEHOLDERS = [
    "Try: Fall brunch specials",
    "Try: Quick lunch menu",
    "Try: New dessert twists",
]


def _resolve_creativity_settings(
    restaurant: "models.Restaurant",
) -> tuple[int, Decimal]:
    """Return the slider value and mapped temperature for a restaurant."""

    settings = getattr(restaurant, "restaurantsettings", None)
    if not settings:
        settings, _ = models.RestaurantSettings.objects.get_or_create(
            restaurant=restaurant
        )

    slider = int(getattr(settings, "classic_creative_slider", 50) or 50)
    slider = max(0, min(100, slider))
    temperature = Decimal("0.1") + Decimal(slider) * Decimal("0.008")
    temperature = temperature.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return slider, temperature

class ConceptList(BaseModel):
    concepts: List[str]


def _ensure_stripe_api_key() -> None:
    """Refresh the Stripe API key from settings for the current process."""

    stripe.api_key = settings.STRIPE_SECRET_KEY or ""


def _get_default_plan() -> models.Plan:
    """Fetch or create the default plan used for subscriptions."""

    defaults = {
        "name": "Pro",
        "limits": {"concept_runs": 100, "dish_runs": 100, "price": "199"},
        "features": [
            "Unlimited menu scrapes",
            "Concept and dish generation",
            "Team collaboration",
        ],
    }
    plan, _ = models.Plan.objects.get_or_create(
        code=getattr(settings, "STRIPE_PLAN_CODE", "pro"), defaults=defaults
    )
    return plan


def _current_season(current_date: Optional[datetime.date] = None) -> str:
    """Return a friendly season label for the given date."""

    if current_date is None:
        try:
            current_date = timezone.localdate()
        except Exception:  # pragma: no cover - fallback for naive environments
            current_date = datetime.date.today()

    month = current_date.month
    if month in {12, 1, 2}:
        return "Winter"
    if month in {3, 4, 5}:
        return "Spring"
    if month in {6, 7, 8}:
        return "Summer"
    return "Autumn"


def build_prompt_suggestions(
    restaurant: Optional[models.Restaurant],
    *,
    max_items: int = 4,
) -> List[str]:
    """Create contextual suggestion chips for the AI input component."""

    seen: set[str] = set()
    suggestions: List[str] = []

    season = _current_season()
    cache_key: Optional[str] = None
    if restaurant and getattr(restaurant, "id", None):
        cache_payload = {
            "restaurant": str(restaurant.id),
            "season": season,
            "context": restaurant.context_json or {},
            "about": restaurant.about_json or {},
            "location": restaurant.location_text,
            "menu_urls": restaurant.menu_urls or [],
            "primary_menu_url": restaurant.primary_menu_url or "",
            "max_items": max_items,
        }
        cache_key = f"prompt-suggestions:{_stable_hash(cache_payload)}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    def _add_suggestion(value: Optional[str]) -> None:
        if not value:
            return
        text = value.strip()
        if not text or text.lower() in seen:
            return
        seen.add(text.lower())
        suggestions.append(text)

    if restaurant:
        context_data = restaurant.context_json or {}
        about_data = restaurant.about_json or {}

        cuisine = context_data.get("category") or context_data.get("cuisine")
        if isinstance(cuisine, (list, tuple)):
            cuisine = cuisine[0] if cuisine else None
        if isinstance(cuisine, str):
            _add_suggestion(f"{cuisine} chef's table")

        city = context_data.get("city") or getattr(restaurant, "location_text", "")
        if isinstance(city, str) and city:
            parts = city.split(",")
            city_name = parts[0].strip()
            if city_name:
                _add_suggestion(f"{city_name} neighborhood favorites")

        review_tags = context_data.get("reviews_tags") or []
        if isinstance(review_tags, list) and review_tags:
            first_tag = str(review_tags[0]).strip()
            if first_tag:
                _add_suggestion(f"Lean into {first_tag} vibes")

        highlights = about_data.get("Highlights") if isinstance(about_data, dict) else {}
        if isinstance(highlights, dict) and highlights:
            first_highlight = next(iter(highlights.keys()), "")
            if first_highlight:
                _add_suggestion(f"Showcase {first_highlight.lower()} partners")

        _add_suggestion(f"{season} market specials")

    fallback_options = [
        "Seasonal chef specials",
        "Comfort classics night",
        "Weekend brunch board",
        "Happy hour upgrades",
    ]

    for option in fallback_options:
        _add_suggestion(option)

    result = list(islice(suggestions, max_items))
    if cache_key:
        cache.set(cache_key, result, timeout=DEFAULT_CACHE_TIMEOUT)
    return result


def _footer_articles(limit: int = 4) -> List[Any]:
    """Return a small set of published articles for footer links."""

    article_model = getattr(models, "Article", None)
    if article_model is None:
        return []

    cache_key = f"footer-articles:{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    articles = list(
        article_model.objects.filter(published_at__isnull=False)
        .order_by("-published_at")[:limit]
    )
    cache.set(cache_key, articles, timeout=DEFAULT_CACHE_TIMEOUT)
    return articles


def _stripe_timestamp(value: Optional[int]) -> datetime.datetime:
    """Convert a Stripe timestamp into an aware datetime."""

    if not value:
        return timezone.now()
    return datetime.datetime.fromtimestamp(value, tz=datetime.timezone.utc)


def _sync_subscription(subscription_data: dict) -> None:
    """Create or update a subscription based on Stripe payload."""

    sub_id = subscription_data.get("id")
    if not sub_id:
        return

    account: Optional[models.Account] = None
    local = models.Subscription.objects.filter(provider_sub_id=sub_id).first()
    if local:
        account = local.account

    metadata = subscription_data.get("metadata") or {}
    if not account:
        account_id = metadata.get("account_id")
        if account_id:
            account = models.Account.objects.filter(id=account_id).first()

    if not account:
        customer_id = subscription_data.get("customer")
        if customer_id:
            account = models.Account.objects.filter(
                stripe_customer_id=customer_id
            ).first()

    if not account:
        return

    customer_id = subscription_data.get("customer")
    if customer_id and account.stripe_customer_id != customer_id:
        account.stripe_customer_id = customer_id
        account.save(update_fields=["stripe_customer_id"])

    plan = _get_default_plan()
    status = subscription_data.get("status", models.Subscription.Status.TRIALING)
    defaults = {
        "plan": plan,
        "provider": models.Subscription.Provider.STRIPE,
        "provider_customer_id": customer_id or "",
        "status": status,
        "current_period_start": _stripe_timestamp(
            subscription_data.get("current_period_start")
        ),
        "current_period_end": _stripe_timestamp(
            subscription_data.get("current_period_end")
        ),
        "cancel_at_period_end": subscription_data.get(
            "cancel_at_period_end", False
        ),
    }

    models.Subscription.objects.update_or_create(
        account=account,
        provider=models.Subscription.Provider.STRIPE,
        provider_sub_id=sub_id,
        defaults=defaults,
    )


def _latest_subscription_for_account(
    account: models.Account,
) -> Optional[models.Subscription]:
    """Return the most recent subscription for an account."""

    return (
        models.Subscription.objects.filter(account=account)
        .order_by("-created_at")
        .first()
    )


def _get_session_list(session, key: str) -> List[str]:
    """Return a list of strings stored under the given session key."""

    value = session.get(key, [])
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _extend_session_list(session, key: str, new_items: Iterable[str]) -> None:
    """Merge unique string items into a session-stored list."""

    existing = _get_session_list(session, key)
    for item in new_items:
        if item and item not in existing:
            existing.append(item)
    session[key] = existing
    session.modified = True


def _load_home_demo_favorites():
    """Return demo favorites (concept + dish) for the marketing homepage."""

    concept = None
    concept_favorite = None
    dish = None
    dish_favorite = None
    restaurant = None

    try:
        demo_user = User.objects.filter(id=DEMO_USER_ID).first()
        if not demo_user:
            return concept, concept_favorite, dish, dish_favorite, restaurant

        concept_favorites = list(
            models.FavoriteConcept.objects.filter(
                user=demo_user, concept__sketch_image_url__isnull=False
            )
            .select_related("concept__restaurant", "concept__ideation_run")
            .order_by("-favorited_at")
        )

        if not concept_favorites:
            return concept, concept_favorite, dish, dish_favorite, restaurant

        concept_by_id = {fav.concept_id: fav for fav in concept_favorites}
        concept_ids = list(concept_by_id.keys())

        dish_favorite = (
            models.FavoriteDish.objects.filter(
                user=demo_user,
                dish__is_deleted=False,
                dish__parent_concept_id__in=concept_ids,
            )
            .select_related("dish__parent_concept__restaurant", "dish__restaurant")
            .order_by("-favorited_at")
            .first()
        )

        if dish_favorite:
            dish = dish_favorite.dish
            decorate_dishes_with_enhancements([dish])
            dish.is_favorited = True
            dish.favorited_at = dish_favorite.favorited_at
            concept_favorite = concept_by_id.get(dish.parent_concept_id)

        if not concept_favorite:
            concept_favorite = concept_favorites[0]

        concept = concept_favorite.concept
        concept.is_favorited_for_user = True
        concept.has_dishes = bool(dish) or getattr(concept, "has_dishes", False)
        concept.favorited_at = concept_favorite.favorited_at
        restaurant = getattr(concept, "restaurant", None)

    except Exception:  # pragma: no cover - defensive to keep landing safe
        logger.exception("Failed to load demo favorites for home view")

    return concept, concept_favorite, dish, dish_favorite, restaurant

from app import llm
from .tasks import parse_pdf_menu, run_outscraper_search, scrape_menu

def dish_grid(request, concept_name: str):
    """Render a 3x3 grid of dishes for a concept."""
    dishes = llm.generate_dishes(concept_name)
    ctx = {"concept": concept_name, "dishes": dishes}
    return render(request, "app/dish_grid.html", ctx)


def home_view(request):
    """Landing page with signup/login links."""
    (
        demo_concept,
        demo_concept_favorite,
        demo_dish,
        demo_dish_favorite,
        demo_restaurant,
    ) = _load_home_demo_favorites()

    context = {
        "demo_concept": demo_concept,
        "demo_concept_favorite": demo_concept_favorite,
        "demo_dish": demo_dish,
        "demo_dish_favorite": demo_dish_favorite,
        "demo_restaurant": demo_restaurant,
        "demo_user_id": DEMO_USER_ID,
        "footer_articles": _footer_articles(),
    }

    return render(request, "home.html", context)


def privacy_view(request):
    """Public privacy policy page."""

    context = {"footer_articles": _footer_articles()}
    return render(request, "privacy.html", context)


def terms_view(request):
    """Public terms of service page."""

    context = {"footer_articles": _footer_articles()}
    return render(request, "terms.html", context)


def contact_view(request):
    """Public contact page."""

    context = {"footer_articles": _footer_articles()}
    return render(request, "contact.html", context)


def signup_view(request):
    """Register a new user and restaurant."""
    if request.method == "POST":
        is_json = request.content_type == "application/json"
        if is_json:
            try:
                data = json.loads(request.body or "{}")
            except json.JSONDecodeError:
                return JsonResponse({"error": "invalid_json"}, status=400)
        else:
            data = request.POST

        email = (data.get("email") or "").strip()
        restaurant_name = (data.get("restaurant_name") or "").strip()
        location = (data.get("location") or "").strip()
        form_data = {
            "email": email,
            "restaurant_name": restaurant_name,
            "location": location,
        }

        if is_json:
            password = data.get("password")
            if not password:
                return JsonResponse({"error": "password_required"}, status=400)
        else:
            password1 = data.get("password1")
            password2 = data.get("password2")
            if password1 != password2:
                return render(
                    request,
                    "auth/signup.html",
                    {"error": "Passwords do not match", "form_data": form_data},
                )
            password = password1

        if not email or not restaurant_name or not location:
            if is_json:
                return JsonResponse({"error": "missing_fields"}, status=400)
            return render(
                request,
                "auth/signup.html",
                {"error": "Please complete all fields.", "form_data": form_data},
            )

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=email, email=email, password=password
                )
                models.UserProfile.objects.create(user=user)
                account = models.Account.objects.create(name=restaurant_name)
                models.Membership.objects.create(
                    account=account, user=user, role=models.Membership.Role.OWNER
                )
                restaurant = models.Restaurant.objects.create(
                    account=account,
                    name=restaurant_name,
                    location_text=location,
                )
                payload = models.OutscraperPayload.objects.create(
                    restaurant=restaurant,
                    status=models.OutscraperPayload.Status.QUEUED,
                    request_params={
                        "query": f"{restaurant_name} {location}",
                        "async": "false",
                        "limit": 1,
                    },
                )
                transaction.on_commit(
                    lambda payload_id=str(payload.id): run_outscraper_search.delay(
                        payload_id
                    )
                )
        except IntegrityError:
            error_message = "An account with that email already exists."
            if is_json:
                return JsonResponse({"error": "email_in_use"}, status=400)
            return render(
                request,
                "auth/signup.html",
                {"error": error_message, "form_data": form_data},
            )

        login(request, user)
        request.session["onboarding_account_id"] = str(account.id)
        request.session["onboarding_restaurant_id"] = str(restaurant.id)
        request.session["just_signed_up"] = True
        redirect_url = reverse("onboarding")
        if is_json:
            return JsonResponse(
                {
                    "redirect_url": redirect_url,
                    "restaurant_id": str(restaurant.id),
                }
            )
        return redirect(redirect_url)

    return render(request, "auth/signup.html")



def login_view(request):
    """Authenticate an existing user."""
    if request.method == "POST":
        username = (
            request.POST.get("username")
            or request.POST.get("email")
            or ""
        ).strip()
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            restaurant_id = (
                models.Restaurant.objects.filter(account__membership__user=user)
                .values_list("id", flat=True)
                .first()
            )
            if restaurant_id:
                return redirect("dashboard", restaurant_id=restaurant_id)
            
        return render(
            request,
            "auth/login.html",
            {
                "error": "We couldn't log you in. Double-check your email and password.",
                "form_data": {"email": username},
            },
        )
    return render(request, "auth/login.html")


@require_http_methods(["GET", "POST"])
def logout_view(request):
    """Log the user out and send them back to the login screen."""
    logout(request)
    redirect_target = getattr(settings, "LOGOUT_REDIRECT_URL", None) or reverse("login")
    return redirect(redirect_target)


@login_required
def dashboard(request, restaurant_id):
    restaurant = get_object_or_404(
        models.Restaurant.objects.select_related("account", "restaurantsettings"),
        id=restaurant_id,
    )

    settings_obj, _ = models.RestaurantSettings.objects.get_or_create(
        restaurant=restaurant
    )
    slider_value, slider_temperature = _resolve_creativity_settings(restaurant)
    slider_temperature_float = float(slider_temperature)
    creative_bias_label = (
        "Creative bias: "
        f"{slider_value}/100 (0 = Classic, 100 = Inventive) · Temp {slider_temperature_float:.2f}"
    )

    subscription = (
        models.Subscription.objects.filter(account=restaurant.account)
        .order_by("-created_at")
        .first()
    )

    trial_info = {
        "label": "Free preview",
        "is_trial": False,
        "ends_at": None,
        "days_remaining": None,
        "hours_remaining": None,
        "countdown_display": None,
        "show_upgrade": True,
        "action_label": "Upgrade plan",
    }

    if subscription:
        status = subscription.status
        display = subscription.get_status_display()
        if status == models.Subscription.Status.ACTIVE:
            trial_info["label"] = "Active plan"
        elif status == models.Subscription.Status.TRIALING:
            trial_info["label"] = "Free trial"
        else:
            trial_info["label"] = display
        trial_info["show_upgrade"] = subscription.status in {
            models.Subscription.Status.TRIALING,
            models.Subscription.Status.PAST_DUE,
            models.Subscription.Status.CANCELED,
        }
        if subscription.status == models.Subscription.Status.ACTIVE:
            trial_info["action_label"] = "Manage billing"
        if subscription.status == models.Subscription.Status.TRIALING:
            trial_info["is_trial"] = True
            trial_info["action_label"] = "Upgrade plan"
            if subscription.current_period_end:
                remaining = subscription.current_period_end - timezone.now()
                total_seconds = int(max(remaining.total_seconds(), 0))
                days = total_seconds // 86400
                hours = (total_seconds % 86400) // 3600
                trial_info.update(
                    {
                        "ends_at": subscription.current_period_end,
                        "days_remaining": days,
                        "hours_remaining": hours,
                        "countdown_display": f"{days}d {hours}h remaining",
                    }
                )
        elif subscription.status == models.Subscription.Status.PAST_DUE:
            trial_info["action_label"] = "Update payment"
        elif subscription.status == models.Subscription.Status.CANCELED:
            trial_info["action_label"] = "Restart plan"
        elif subscription.status not in {
            models.Subscription.Status.PAST_DUE,
            models.Subscription.Status.CANCELED,
        }:
            trial_info["show_upgrade"] = False

    concepts_qs = (
        models.Concept.objects.filter(restaurant=restaurant)
        .select_related("ideation_run")
        .annotate(
            has_dishes=Exists(
                models.DishIdea.objects.filter(
                    parent_concept=OuterRef("pk"), is_deleted=False
                )
            )
        )
        .order_by("-created_at")
    )
    recent_concepts = list(concepts_qs[:4])
    for concept in recent_concepts:
        concept.runtime_display = format_run_duration(concept.ideation_run)
        run_finished = (
            concept.ideation_run.finished_at if concept.ideation_run else None
        )
        concept.generated_at = run_finished or concept.created_at

    favorite_dishes = list(
        models.FavoriteDish.objects.filter(
            user=request.user, dish__restaurant=restaurant
        )
        .select_related("dish__parent_concept")
        .order_by("-favorited_at")[:4]
    )
    dishes_only = [fav.dish for fav in favorite_dishes]
    recent_dishes = decorate_dishes_with_enhancements(dishes_only)
    for fav, dish in zip(favorite_dishes, recent_dishes):
        dish.is_favorited = True
        dish.favorited_at = fav.favorited_at

    menus = list(
        models.MenuCollection.objects.filter(restaurant=restaurant)
        .prefetch_related(
            Prefetch(
                "menuitem_set",
                queryset=models.MenuItem.objects.select_related(
                    "dish",
                    "dish__parent_concept",
                ).order_by("position", "created_at"),
            )
        )
        .order_by("-created_at")[:4]
    )
    for menu in menus:
        items = [
            item
            for item in menu.menuitem_set.all()
            if item.dish and not item.dish.is_deleted
        ]
        menu.menu_items = items

    context_items = build_context_items(restaurant, settings_obj)

    context = {
        "restaurant": restaurant,
        "trial_info": trial_info,
        "context_items": context_items,
        "recent_concepts": recent_concepts,
        "recent_dishes": recent_dishes,
        "menus": menus,
        "empty_concepts": [],
        "settings_url": reverse("settings"),
        "context_toggle_url": reverse(
            "dashboard-context-toggle", args=[restaurant.id]
        ),
        "tbd_message": "Personalized tips will appear here soon.",
        "prompt_for_menu": not bool(restaurant.primary_menu_url),
        "concept_generate_url": reverse("concepts-generate"),
        "concept_prompt_placeholders": DEFAULT_PROMPT_PLACEHOLDERS,
        "concept_prompt_suggestions": build_prompt_suggestions(restaurant),
        "classic_creative_slider": slider_value,
        "classic_creative_temperature": slider_temperature_float,
        "creative_bias_label": creative_bias_label,
    }
    return render(request, "dashboard.html", context)


@login_required
@require_POST
def dashboard_context_toggle(request, restaurant_id):
    restaurant = get_object_or_404(models.Restaurant, id=restaurant_id)
    membership_exists = models.Membership.objects.filter(
        account=restaurant.account, user=request.user
    ).exists()
    if not membership_exists:
        return HttpResponseForbidden("not_allowed")

    settings_obj, _ = models.RestaurantSettings.objects.get_or_create(
        restaurant=restaurant
    )

    key = request.POST.get("key")
    valid_keys = {item[0] for item in CONTEXT_ITEM_DEFINITIONS}
    if key not in valid_keys:
        return HttpResponseBadRequest("invalid_key")

    include_values = request.POST.getlist("include")
    include_raw = include_values[-1] if include_values else "false"
    include = str(include_raw).lower() in {"1", "true", "yes", "on"}

    context_flags = dict(settings_obj.llm_defaults.get("context_flags", {}))
    context_flags[key] = include
    settings_obj.llm_defaults["context_flags"] = context_flags
    settings_obj.save(update_fields=["llm_defaults"])

    context_items = build_context_items(restaurant, settings_obj)
    item = next((item for item in context_items if item["key"] == key), None)
    if not item:
        return HttpResponseBadRequest("unknown_item")

    html = render_to_string(
        "dashboard/_context_item.html",
        {
            "item": item,
            "settings_url": reverse("settings"),
            "toggle_url": reverse("dashboard-context-toggle", args=[restaurant.id]),
        },
        request=request,
    )
    return HttpResponse(html)


@login_required
def dashboard_redirect(request):
    """Send the user to their first restaurant dashboard."""
    restaurant_id = (
        models.Restaurant.objects.filter(account__membership__user=request.user)
        .values_list("id", flat=True)
        .first()
    )
    if restaurant_id:
        return redirect("dashboard", restaurant_id=restaurant_id)
    return redirect("home")


@login_required
def menus_view(request):
    """Render menus page."""
    restaurant = (
        models.Restaurant.objects.filter(account__membership__user=request.user)
        .select_related("account")
        .first()
    )

    menus: List[models.MenuCollection] = []
    if restaurant:
        menus = list(
            models.MenuCollection.objects.filter(restaurant=restaurant)
            .prefetch_related(
                Prefetch(
                    "menuitem_set",
                    queryset=models.MenuItem.objects.select_related(
                        "dish",
                        "dish__parent_concept",
                        "dish__restaurant",
                    ).order_by("position", "created_at"),
                )
            )
            .order_by("created_at")
        )
        for menu in menus:
            items = list(menu.menuitem_set.all())
            menu.menu_items = items

        menu_ids = [menu.id for menu in menus]
        active_links = {
            link.menu_id: link
            for link in models.CollaborationLink.objects.filter(
                menu_id__in=menu_ids, is_active=True
            )
        }
        pending_counts = {
            row["feedback__menu_id"]: row["count"]
            for row in models.FeedbackAction.objects.filter(
                feedback__menu_id__in=menu_ids,
                status=models.FeedbackAction.Status.PENDING,
            )
            .values("feedback__menu_id")
            .annotate(count=Count("id"))
        }
        for menu in menus:
            link = active_links.get(menu.id)
            menu.collaboration_link = link
            menu.pending_feedback_count = pending_counts.get(menu.id, 0)
            if link:
                menu.collaboration_url = request.build_absolute_uri(
                    reverse("collaboration-dashboard", args=[link.id])
                )

    all_dishes = [
        item.dish for menu in menus for item in getattr(menu, "menu_items", [])
    ]
    decorate_dishes_with_enhancements(all_dishes)
    for dish in all_dishes:
        dish.is_favorited = True

    menu_options = [
        {
            "id": str(menu.id),
            "name": menu.name,
        }
        for menu in menus
    ]

    ctx = {
        "restaurant": restaurant,
        "menus": menus,
        "menu_options": menu_options,
        "menu_move_url": reverse("menu-item-move"),
        "menus_workspace_url": reverse("menus"),
    }
    return render(request, "menus/main.html", ctx)


@login_required
def onboarding_view(request):
    """Show onboarding progress and billing step."""

    membership = (
        models.Membership.objects.filter(user=request.user)
        .select_related("account")
        .first()
    )
    account = membership.account if membership else None
    restaurant = None
    subscription = None
    latest_payload = None
    latest_menu_version = None
    menu_success = False
    menu_error = ""

    if account:
        restaurant = (
            models.Restaurant.objects.filter(account=account)
            .order_by("created_at")
            .first()
        )
        subscription = (
            models.Subscription.objects.filter(account=account)
            .order_by("-created_at")
            .first()
        )

    if restaurant:
        latest_payload = (
            models.OutscraperPayload.objects.filter(restaurant=restaurant)
            .order_by("-created_at")
            .first()
        )
        latest_menu_version = (
            models.MenuVersion.objects.filter(restaurant=restaurant)
            .order_by("-created_at")
            .first()
        )
        if latest_menu_version and latest_menu_version.status == models.MenuVersion.Status.SUCCEEDED:
            menu_success = True

    if request.method == "POST":
        menu_url = (request.POST.get("menu_url") or "").strip()
        if not restaurant:
            menu_error = "We couldn't find your restaurant record yet."
        elif not menu_url:
            menu_error = "Please provide a menu URL."
        else:
            restaurant.add_menu_url(menu_url)
            restaurant.save(update_fields=["menu_urls", "primary_menu_url"])
            latest_menu_version = models.MenuVersion.objects.create(
                restaurant=restaurant,
                source_url=menu_url,
                source_kind=models.MenuVersion.SourceKind.URL_SCRAPE,
                raw_markdown="",
                status=models.MenuVersion.Status.QUEUED,
            )
            transaction.on_commit(
                lambda mv_id=str(latest_menu_version.id): scrape_menu.delay(mv_id)
            )
            menu_success = True

    if request.session.get("menu_success"):
        menu_success = True
        request.session.pop("menu_success")

    just_signed_up = request.session.pop("just_signed_up", False)
    if request.method == "POST" and just_signed_up and not menu_success:
        request.session["just_signed_up"] = True

    subscription_allows_dashboard = False
    subscription_requires_update = False
    if subscription:
        subscription_allows_dashboard = subscription.status in {
            models.Subscription.Status.ACTIVE,
            models.Subscription.Status.TRIALING,
        }
        subscription_requires_update = not subscription_allows_dashboard

    trial_days = getattr(settings, "STRIPE_TRIAL_DAYS", 14)
    trial_ends = subscription.current_period_end if subscription else None
    trial_days_remaining = None
    if subscription and subscription.status == models.Subscription.Status.TRIALING:
        remaining = subscription.current_period_end - timezone.now()
        trial_days_remaining = max(remaining.days, 0)

    dashboard_url = reverse("dashboard", args=[restaurant.id]) if restaurant else ""

    context = {
        "restaurant": restaurant,
        "subscription": subscription,
        "trial_days": trial_days,
        "trial_ends": trial_ends,
        "trial_days_remaining": trial_days_remaining,
        "show_start_trial": not subscription
        or subscription.status == models.Subscription.Status.CANCELED,
        "outscraper_payload": latest_payload,
        "latest_menu_version": latest_menu_version,
        "menu_success": menu_success,
        "menu_error": menu_error,
        "just_signed_up": just_signed_up,
        "dashboard_url": dashboard_url,
        "subscription_allows_dashboard": subscription_allows_dashboard,
        "subscription_requires_update": subscription_requires_update,
    }
    return render(request, "onboarding.html", context)


@login_required
def onboarding_status_view(request):
    """Return simple onboarding status."""

    membership = models.Membership.objects.filter(user=request.user).first()
    subscription = None
    if membership:
        subscription = (
            models.Subscription.objects.filter(account=membership.account)
            .order_by("-created_at")
            .first()
        )

    return JsonResponse(
        {
            "status": "pending" if not subscription else subscription.status,
            "subscription_started": bool(subscription),
        }
    )


@login_required
def manual_menu_view(request):
    """Allow manual menu entry."""

    membership = (
        models.Membership.objects.filter(user=request.user)
        .select_related("account")
        .first()
    )
    restaurant = None
    if membership:
        restaurant = (
            models.Restaurant.objects.filter(account=membership.account)
            .order_by("created_at")
            .first()
        )

    errors = []
    menu_text = (request.POST.get("menu_text") or "").strip() if request.method == "POST" else ""

    if not restaurant:
        errors.append("We couldn't find a restaurant for your account yet.")
    elif request.method == "POST":
        menu_pdf = request.FILES.get("menu_pdf")
        if not menu_text and not menu_pdf:
            errors.append("Paste your menu or upload a PDF so we can ingest it.")
        else:
            menu_version = _process_menu_submission(restaurant, None, menu_text, menu_pdf)
            if menu_version:
                request.session["menu_success"] = True
                if request.headers.get("HX-Request"):
                    response = HttpResponse(status=204)
                    response["HX-Redirect"] = reverse("onboarding")
                    return response
                return redirect("onboarding")
            errors.append("We couldn't process your submission. Please try again.")

    status = 400 if errors else 200
    context = {"errors": errors, "menu_text": menu_text}
    return render(request, "_partials/manual_menu.html", context, status=status)


@login_required
def concepts_view(request):
    """Display latest concepts with favorite state for the user."""
    membership = models.Membership.objects.filter(user=request.user).select_related(
        "account"
    ).first()
    restaurant = None
    if membership:
        restaurant = (
            models.Restaurant.objects.filter(account=membership.account)
            .order_by("created_at")
            .first()
        )

    slider_value = 50
    slider_temperature = 0.5
    creative_bias_label = ""
    if restaurant:
        slider_value, temperature_decimal = _resolve_creativity_settings(restaurant)
        slider_temperature = float(temperature_decimal)
        creative_bias_label = (
            "Creative bias: "
            f"{slider_value}/100 (0 = Classic, 100 = Inventive) · Temp {slider_temperature:.2f}"
        )

    concepts_qs = models.Concept.objects.order_by("-created_at").annotate(
        has_dishes=Exists(
            models.DishIdea.objects.filter(
                parent_concept=OuterRef("pk"), is_deleted=False
            )
        )
    )
    if request.user.is_authenticated:
        concepts_qs = concepts_qs.prefetch_related(
            Prefetch(
                "favoriteconcept_set",
                queryset=models.FavoriteConcept.objects.filter(user=request.user),
                to_attr="_favorites_for_request_user",
            )
        )
    concepts = list(concepts_qs[:9])
    for concept in concepts:
        favorites = getattr(concept, "_favorites_for_request_user", [])
        concept.is_favorited_for_user = bool(favorites)
    return render(
        request,
        "concepts/grid.html",
        {
            "concepts": concepts,
            "restaurant": restaurant,
            "concept_generate_url": reverse("concepts-generate"),
            "concept_prompt_placeholders": DEFAULT_PROMPT_PLACEHOLDERS,
            "concept_prompt_suggestions": build_prompt_suggestions(restaurant),
            "classic_creative_slider": slider_value,
            "classic_creative_temperature": slider_temperature,
            "creative_bias_label": creative_bias_label,
        },
    )


@login_required
def tag_search_view(request):
    """Search concepts and dishes by tag for the current user's restaurants."""

    search_tag = (request.GET.get("tag") or request.GET.get("q") or "").strip()
    membership = models.Membership.objects.filter(user=request.user).select_related(
        "account"
    ).first()

    restaurant_ids: List[str] = []
    if membership:
        restaurant_ids = list(
            models.Restaurant.objects.filter(account=membership.account)
            .values_list("id", flat=True)
        )

    concept_results: List[models.Concept] = []
    dish_results: List[models.DishIdea] = []

    if search_tag and restaurant_ids:
        concept_results = list(
            models.Concept.objects.filter(restaurant_id__in=restaurant_ids)
            .annotate(tags_text=Cast("tags", TextField()))
            .filter(tags_text__icontains=search_tag)
            .order_by("-created_at")[:25]
        )

        dish_results = list(
            models.DishIdea.objects.filter(
                restaurant_id__in=restaurant_ids, is_deleted=False
            )
            .annotate(
                category_text=Cast("category_tags", TextField()),
                ingredient_text=Cast("ingredient_names", TextField()),
            )
            .filter(
                Q(category_text__icontains=search_tag)
                | Q(ingredient_text__icontains=search_tag)
            )
            .select_related("parent_concept")
            .order_by("-created_at")[:25]
        )

    context = {
        "search_tag": search_tag,
        "concept_results": concept_results,
        "dish_results": dish_results,
    }
    return render(request, "search/results.html", context)


@login_required
def concepts_generate_view(request):
    membership = models.Membership.objects.filter(user=request.user).first()
    restaurant = models.Restaurant.objects.filter(account=membership.account).first()
    raw_prompt = (request.POST.get("prompt") or "").strip()
    user_prompt = raw_prompt[:280]

    slider_value, slider_temperature = _resolve_creativity_settings(restaurant)
    slider_override_raw = request.POST.get("classic_creative_slider")
    if slider_override_raw is not None:
        try:
            slider_override = int(slider_override_raw)
        except (TypeError, ValueError):
            slider_override = None
        if slider_override is not None:
            slider_override = max(0, min(100, slider_override))
            slider_value = slider_override
            slider_temperature = (
                Decimal("0.1") + Decimal(slider_value) * Decimal("0.008")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    temperature_float = float(slider_temperature)

    session_concepts = [
        name.strip()
        for name in (request.session.get("generated_concepts") or [])
        if name and str(name).strip()
    ]
    previous_concepts = list(
        models.Concept.objects.filter(restaurant=restaurant)
        .order_by("-created_at")
        .values_list("name", flat=True)[:27]
    )

    logger.info(f"previous concepts: {previous_concepts}")
    if restaurant.active_menu_version:
        restaurant_menu = restaurant.active_menu_version.raw_markdown
    else:
        restaurant_menu = ""
    context = f"""
    Restaurant: {restaurant.name}, {restaurant.location_text}.  \n
    Description: {restaurant.description}. \n
    Current Restaurant Menu:  {restaurant_menu}
    About Services:  {restaurant.about_json}
    """
    context += (
        "\nCreative direction slider: "
        f"{slider_value}/100 (0 = classic, 100 = highly inventive)."
    )
    if session_concepts:
        context += (
            "\nPreviously generated concept names to avoid: "
            + ", ".join(session_concepts[:15])
        )
    if user_prompt:
        context += f"\nUser special instructions: {user_prompt}"
    # Schema definition for structured output
    schema = {
        "name": "concept_list",
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                "concepts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string",},
                            "subtitle": {"type": "string"},
                            "reasoning": {"type": "string" },
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                                "maxItems": 3
                            }
                        },
                        "required": ["title", "subtitle", "reasoning", "tags"],
                        "additionalProperties": False
                    },
                    "minItems": 9,
                    "maxItems": 9
                }
            },
            "required": ["concepts"],
            "additionalProperties": False,
        },
        "strict": True,
    }



    system_prompt = f"""
                **Role**: You are a seasoned restaurant marketing consultant with deep knowledge of regional cuisines, seasonal ingredients, and cultural dining traditions.
                **Task**: Generate exactly 9 unique, theme-based concepts for daily specials that emphasize regional flavors and seasonal ingredients.

                **Format Requirements for Each Concept**:
                - **Name**: Maximum 30 characters
                - **Subtitle**: Maximum 80 characters (descriptive tagline)
                - **Reasoning**: Explain your creative process and mindset when selecting this concept (maximum 80 characters)
                - **Tags**: Array of 3 relevant keywords that connect the concept to user context

                **Concept Guidelines**:
                - It should be relevant to the users restaurants menu, not identical but within the same style.
                - Focus on THEMES, not individual dishes (like "Taco Tuesday" or "Mediterranean Monday")
                - Emphasize regional specialties around: {restaurant.location_text} 
                - and seasonal ingredients: {datetime.date.today()}
                - Consider cultural celebrations, harvest seasons, and local food traditions
                - Think beyond basic concepts to include:
                - Regional American cuisines (Southern, Pacific Northwest, Southwest, etc.)
                - Seasonal produce celebrations (Spring asparagus, Fall harvest, Summer stone fruits)
                - Cultural heritage nights (Italian Nonna Night, Korean Comfort, etc.)
                - Weather-responsive themes (Cozy Soup Sundays, Summer Grill Nights)

                **Creative Direction Slider**: {slider_value}/100 where 0 = classic and 100 = highly inventive.
                Match the ambition of the slider when balancing comforting favorites with bold experimentation.

                **Example Structure**:
                ```
                1. **Name**: "Harvest Moon Monday"
                **Subtitle**: "Celebrating autumn's bounty with locally-sourced seasonal ingredients"
                **Reasoning**: "Captured the cozy autumn feeling and farm-to-table movement"
                **Tags**: [seasonal, autumn, local-sourcing, comfort-food, farm-to-table, harvest, cozy, regional]
                ```

                **Goal**: Create concepts that restaurant owners can easily adapt to their local region and seasonal availability while building customer excitement and loyalty.

    """

    if previous_concepts:
        system_prompt += (
            " The user has already explored these concept names. Do not repeat or "
            "closely duplicate them: "
            + ", ".join(previous_concepts)
            + "."
        )

    if user_prompt:
        system_prompt += (
            "\n                **Special Focus**: Highlight concepts inspired by: "
            + user_prompt
        )

    context_snapshot = {
        "prompt": user_prompt,
        "session_concepts": session_concepts[:15],
        "context": context,
        "classic_creative_slider": slider_value,
        "temperature": temperature_float,
    }

    ideation_run = models.IdeationRun.objects.create(
        restaurant=restaurant,
        initiated_by_user=request.user,
        type=models.IdeationRun.RunType.CONCEPTS,
        model_name="gpt-4.1-mini",
        temperature=slider_temperature,
        classic_creative=slider_value,
        context_snapshot=context_snapshot,
        status=models.IdeationRun.Status.RUNNING,
        started_at=timezone.now(),)

    concepts: List[models.Concept] = []

    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {"role": "user", "content": context},
            ],
            text={"format": schema},
            temperature=temperature_float,
        )
        logger.info(context)
        raw_text = response.output[0].content[0].text
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            data = {"concepts": []}

        names = data.get("concepts", [])

        concepts = [
            models.Concept.objects.create(
                restaurant=restaurant,
                ideation_run=ideation_run,
                name=item["title"],
                subtitle=item["subtitle"],
                reasoning=item["reasoning"],
                tags=item["tags"],
                rank_order=idx,
            )
            for idx, item in enumerate(names, start=1)
        ]

        ideation_run.status = models.IdeationRun.Status.SUCCEEDED
        ideation_run.finished_at = timezone.now()
        ideation_run.save(update_fields=["status", "finished_at"])
    except Exception as exc:
        ideation_run.status = models.IdeationRun.Status.FAILED
        ideation_run.error_message = str(exc)
        ideation_run.finished_at = timezone.now()
        ideation_run.save(update_fields=["status", "error_message", "finished_at"])
        raise


    for concept in concepts:
        concept.is_favorited_for_user = False
        concept.has_dishes = False

    if request.user.is_authenticated:
        _extend_session_list(
            request.session,
            "generated_concepts",
            [concept.name for concept in concepts],
        )

    response = render(
        request,
        "concepts/_concepts_grid.html",
        {
            "concepts": concepts,
            "concept_generate_url": reverse("concepts-generate"),
        },
    )

    if request.headers.get("HX-Request") == "true":
        current_url = request.headers.get("HX-Current-URL", "")
        if "/dashboard/" in current_url:
            response["HX-Redirect"] = reverse("concepts")
        return response

    return redirect("concepts")

@login_required
def concept_favorite_view(request, concept_id):
    concept = get_object_or_404(models.Concept, id=concept_id)
    fav, created = models.FavoriteConcept.objects.get_or_create(
        user=request.user, concept=concept, defaults={"favorited_at": timezone.now()}
    )

    if created:
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response["HX-Redirect"] = reverse("dish_detail", args=[concept.id])
            return response
        return redirect("dish_detail", concept_id=concept.id)

    favorited = False
    if not created:
        fav.delete()
        if concept.sketch_image_url:
            concept.sketch_image_url = None
            concept.save(update_fields=["sketch_image_url"])

    concept.is_favorited_for_user = favorited
    concept.has_dishes = models.DishIdea.objects.filter(
        parent_concept=concept, is_deleted=False
    ).exists()

    # Always return the refreshed card so the UI stays in sync
    card_html = render_to_string(
        "concepts/_card.html",
        {"concept": concept, "loading": favorited},
        request=request,
    )
    return HttpResponse(card_html)



@login_required
@require_GET
def concept_background_view(request, concept_id):
    """Return the lazy-loaded background sketch for a concept card."""

    concept = get_object_or_404(models.Concept, id=concept_id)
    image_url = concept.sketch_image_url
    if not image_url:
        image_url = llm.generate_concept_sketch(concept)
        concept.sketch_image_url = image_url
        concept.save(update_fields=["sketch_image_url"])

    concept.is_favorited_for_user = models.FavoriteConcept.objects.filter(
        user=request.user, concept=concept
    ).exists()
    concept.has_dishes = models.DishIdea.objects.filter(
        parent_concept=concept, is_deleted=False
    ).exists()

    return render(
        request,
        "concepts/_card.html",
        {"concept": concept},
    )


@login_required
@require_GET
def concepts_favorites_view(request):
    """Return favorited concepts rendered for the concepts page."""

    favorites = (
        models.FavoriteConcept.objects.filter(user=request.user)
        .select_related("concept", "concept__restaurant")
        .order_by("-favorited_at")
    )

    concepts = []
    for favorite in favorites:
        concept = favorite.concept
        if concept is None:
            continue
        concept.is_favorited_for_user = True
        concepts.append(concept)

    concept_ids = [concept.id for concept in concepts]
    if concept_ids:
        concepts_with_dishes = set(
            models.DishIdea.objects.filter(
                parent_concept_id__in=concept_ids, is_deleted=False
            )
            .values_list("parent_concept_id", flat=True)
        )
    else:
        concepts_with_dishes = set()

    for concept in concepts:
        concept.has_dishes = concept.id in concepts_with_dishes

    return render(
        request,
        "concepts/_favorites_section.html",
        {"concepts": concepts},
    )

def serialize_restaurant_context(restaurant, concept, request=None):
    """
    Return a slim JSON-serializable context for dish generation.
    """

    raw_key = f"context:{restaurant.id}:{concept.id}:{getattr(request, 'session', {}).session_key}"
    cache_key = hashlib.md5(raw_key.encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached:
        return cached

    ctx_json = restaurant.context_json or {}
    about = restaurant.about_json or {}

    # Pick only a few relevant fields
    context = {
        "restaurant": {
            "name": restaurant.name,
            "description": restaurant.description,
            "category": ctx_json.get("category"),
            "price_range": ctx_json.get("range"),
            "city": ctx_json.get("city"),
            "state": ctx_json.get("us_state"),
            "rating": ctx_json.get("rating"),
            "reviews_tags": ctx_json.get("reviews_tags", [])[:5],  # top 5 tags
            "highlights": list((about.get("Highlights") or {}).keys()),
            "atmosphere": list((about.get("Atmosphere") or {}).keys()),
        },
        "menu_markdown": (
            restaurant.active_menu_version.raw_markdown
            if restaurant.active_menu_version else ""
        ),
        "concept": {
            "id": str(concept.id) if concept else None,
            "name": concept.name if concept else None,
        },
    }

    cache.set(cache_key, context, timeout=600)
    return context


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


def decorate_dishes_with_enhancements(dishes: Iterable[models.DishIdea],) -> List[models.DishIdea]:
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


CONTEXT_ITEM_DEFINITIONS = (
    ("menu", "Menu link"),
    ("menu_content", "Menu content"),
    ("services", "Services info"),
    ("story", "Story & description"),
    ("reviews", "Guest reviews"),
    ("ingredients", "Ingredient list"),
)


def _context_items_cache_key(
    restaurant: models.Restaurant, settings_obj: models.RestaurantSettings
) -> str:
    payload = {
        "restaurant": str(getattr(restaurant, "id", "")),
        "settings": settings_obj.llm_defaults,
        "settings_updated": (
            settings_obj.updated_at.isoformat() if getattr(settings_obj, "updated_at", None) else ""
        ),
        "menu_version": str(
            getattr(getattr(restaurant, "active_menu_version", None), "id", "")
        ),
        "menu_urls": restaurant.menu_urls or [],
        "primary_menu_url": restaurant.primary_menu_url or "",
        "review_count": restaurant.review_count,
        "rating": str(restaurant.rating) if restaurant.rating is not None else "",
        "context": restaurant.context_json or {},
        "about": restaurant.about_json or {},
    }
    return f"context-items:{_stable_hash(payload)}"


def build_context_items(
    restaurant: models.Restaurant, settings_obj: models.RestaurantSettings
) -> List[dict]:
    """Return context checklist items with presence + preference state."""

    cache_key = _context_items_cache_key(restaurant, settings_obj)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    context_flags = dict(settings_obj.llm_defaults.get("context_flags", {}))
    items: List[dict] = []

    def _has_content(value) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, dict):
            return any(_has_content(item) for item in value.values())
        if isinstance(value, (list, tuple, set)):
            return any(_has_content(item) for item in value)
        return bool(value)

    menu_urls = [url for url in (restaurant.menu_urls or []) if (url or "").strip()]
    has_menu_link = bool(restaurant.primary_menu_url or menu_urls)

    if (
        restaurant.active_menu_version
        and _has_content(restaurant.active_menu_version.raw_markdown)
    ):
        has_menu_content = True
    else:
        has_menu_content = models.MenuVersion.objects.filter(
            restaurant=restaurant,
            raw_markdown__isnull=False,
        ).exclude(raw_markdown="").exists()

    about_data = restaurant.about_json or {}
    services_sections = []
    if isinstance(about_data, dict):
        for key, value in about_data.items():
            if isinstance(key, str) and "service" in key.lower():
                services_sections.append(value)
    has_services_info = any(_has_content(section) for section in services_sections)

    context_data = restaurant.context_json or {}
    review_sources = [
        restaurant.review_count,
        restaurant.rating,
    ]
    if isinstance(context_data, dict):
        review_sources.extend(
            context_data.get(key)
            for key in ["reviews", "reviews_tags", "review_snippets"]
            if key in context_data
        )
    has_reviews = any(_has_content(value) for value in review_sources)

    has_ingredients = models.Ingredient.objects.filter(restaurant=restaurant).exists()

    presence_map = {
        "menu": has_menu_link,
        "menu_content": has_menu_content,
        "services": has_services_info,
        "story": _has_content(restaurant.description),
        "reviews": has_reviews,
        "ingredients": has_ingredients,
    }

    updated = False
    for key, _ in CONTEXT_ITEM_DEFINITIONS:
        if key not in context_flags:
            context_flags[key] = presence_map.get(key, False)
            updated = True

    if updated:
        settings_obj.llm_defaults["context_flags"] = context_flags
        settings_obj.save(update_fields=["llm_defaults"])

    for key, label in CONTEXT_ITEM_DEFINITIONS:
        present = presence_map.get(key, False)
        include_preference = bool(context_flags.get(key))
        included = include_preference and present
        status = "missing"
        if present and included:
            status = "included"
        elif present:
            status = "excluded"
        items.append(
            {
                "key": key,
                "label": label,
                "present": present,
                "included": included,
                "status": status,
            }
        )

    cache_key = _context_items_cache_key(restaurant, settings_obj)
    cache.set(cache_key, items, timeout=SHORT_CACHE_TIMEOUT)
    return items


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


def ensure_dish_enhancement(dish: models.DishIdea, user: Optional[User]) -> Optional[models.Enhancement]:
    """Create an enhancement for the dish if one does not already exist."""

    existing = (
        models.Enhancement.objects.filter(
            dish=dish, status=models.Enhancement.Status.SUCCEEDED
        )
        .select_related("image_asset")
        .order_by("-created_at")
        .first()
    )
    if existing:
        return existing

    try:
        payload = llm.enhance_dish(dish, dish.restaurant)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("Enhancement request failed: %s", exc, exc_info=True)
        return None

    image_url = _fetch_gemini_image(
            prompt=f"Plated dish photo of {dish.title}: {dish.description}",
            default_url=llm.DEFAULT_IMAGE_URL,
        )
    price_cents = payload.get("price_cents")
    currency = payload.get("currency") or "USD"
    pricing_notes = payload.get("pricing_notes")
    style_preset = payload.get("style_preset") or "enhanced-mode-v1"
    model_name = payload.get("model_name") or "enhanced-mode"

    enhancement = models.Enhancement.objects.create(
        dish=dish,
        triggered_by_user=user,
        status=models.Enhancement.Status.SUCCEEDED,
        suggested_price_cents=price_cents,
        currency=currency,
        pricing_notes=pricing_notes,
        style_preset=style_preset,
        model_name=model_name,
        started_at=timezone.now(),
        finished_at=timezone.now(),
    )

    if image_url:
        asset = models.Asset.objects.create(
            kind=models.Asset.Kind.IMAGE,
            storage_key=f"enhanced/{dish.id}/{uuid.uuid4()}",
            public_url=image_url,
        )
        enhancement.image_asset = asset
        enhancement.save(update_fields=["image_asset"])

    return enhancement

@login_required
def dishes_generate_view(request, concept_id):
    """Generate nine dish ideas for a concept and return updated content."""
    concept = models.Concept.objects.select_related("restaurant").get(id=concept_id)
    restaurant = concept.restaurant
    membership = models.Membership.objects.filter(user=request.user).first()
    htmx_request = request.headers.get("HX-Request") == "true"
    slider_value, slider_temperature = _resolve_creativity_settings(restaurant)
    temperature_float = float(slider_temperature)
    if restaurant.active_menu_version:
        restaurant_menu = restaurant.active_menu_version.raw_markdown
    else:
        restaurant_menu = ""

    context_text = f"""
        Restaurant: {restaurant.name}, {restaurant.location_text}.  \n
        Description: {restaurant.description}. \n
        Current Restaurant Menu:  {restaurant_menu}
        About Services:  {restaurant.about_json}
    """
    context_text += (
        "\n        Creative direction slider: "
        f"{slider_value}/100 (0 = classic, 100 = highly inventive)."
    )

    deleted_dishes = list(
        models.DishIdea.objects.filter(restaurant=restaurant, is_deleted=True)
        .order_by("-created_at")
        .values_list("title", flat=True)[:15]
    )

    context_payload = {
        "context": context_text,
        "deleted_dishes": deleted_dishes,
        "classic_creative_slider": slider_value,
        "temperature": temperature_float,
    }

    logger.info("Starting dish generation: concept=%s, restaurant=%s", concept.name, restaurant.name)

    # Build previous dish titles (avoid duplication in generation)
    previous_titles: List[str] = list(
        models.DishIdea.objects.filter(restaurant=restaurant, is_deleted=False)
        .order_by("-created_at")
        .values_list("title", flat=True)[:27]
    )

    #context = serialize_restaurant_context(restaurant, concept, request=request)
    logger.info("Context: %s", context_payload)
    # Prepare schema
    schema = {
        "name": "dish_list",
        "type": "json_schema",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "dishes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "ingredient_overlap": {
                                "type": "array", "items": {"type": "string"}
                            },
                            "category_tags": {
                                "type": "array", "items": {"type": "string"}
                            },
                        },
                        "required": ["title", "description", "ingredient_overlap", "category_tags"],
                        "additionalProperties": False,
                    },
                    "minItems": 9,
                    "maxItems": 9,
                }
            },
            "required": ["dishes"],
            "additionalProperties": False,
        },
    }
    # Create IdeationRun *before* calling LLM so we can track errors
    ideation_run = models.IdeationRun.objects.create(
        restaurant=restaurant,
        initiated_by_user=request.user,
        type=models.IdeationRun.RunType.DISHES,
        model_name="gpt-4o-mini",
        temperature=slider_temperature,
        classic_creative=slider_value,
        context_snapshot=context_payload,
        parent_concept=concept,
        status=models.IdeationRun.Status.RUNNING,
    )

    dish_objects: List[models.DishIdea] = []

    try:
        # Build instruction text
        instruction = f"""
        Given the following restaurant context and menu, generate 9 saleable dish ideas
        for the concept: '{concept.name}'.
        Each dish must include: title, description, ingredient_overlap, category_tags.
        """

        instruction += (
            "\nCreative direction slider: "
            f"{slider_value}/100 (0 = classic, 100 = highly inventive)."
            " Follow this bias when proposing dish riffs."
        )

        if previous_titles:
            instruction += "\nAvoid repeating these dish names: " + ", ".join(previous_titles)

        # Call LLM
        response = client.responses.create(
            model="gpt-4o-mini",
            input=[
                {"role": "user", "content": instruction},
                {"role": "user", "content": json.dumps(context_payload, indent=2)},
            ],
            text={"format": schema},
            temperature=temperature_float,
        )

        raw_text = response.output[0].content[0].text
        parsed = json.loads(raw_text)
        dishes = parsed["dishes"]

        logger.info("LLM generated %d dishes for concept=%s", len(dishes), concept.name)

        # Persist dish ideas
        for dish in dishes:
            obj = models.DishIdea.objects.create(
                restaurant=restaurant,
                ideation_run=ideation_run,
                parent_concept=concept,
                title=dish["title"],
                description=dish["description"],
                ingredient_names=dish["ingredient_overlap"],
                category_tags=dish["category_tags"],
            )
            dish_objects.append(obj)

        # Mark run as successful
        ideation_run.status = models.IdeationRun.Status.SUCCEEDED
        ideation_run.save(update_fields=["status"])
        logger.info("Dish generation succeeded: %d dishes stored (run_id=%s)", len(dish_objects), ideation_run.id)

        # Save generated dish names into session history
        if request.user.is_authenticated:
            _extend_session_list(
                request.session,
                "generated_dishes",
                [dish.title for dish in dish_objects],
            )

    except Exception as e:
        logger.error("Dish generation failed for concept=%s: %s", concept.name, str(e), exc_info=True)
        ideation_run.status = models.IdeationRun.Status.FAILED
        ideation_run.error_message = str(e)
        ideation_run.save(update_fields=["status", "error_message"])

    if htmx_request:
        response = HttpResponse(status=204)
        response["HX-Redirect"] = reverse("dish_detail", args=[concept.id])
        return response

    return dish_detail_view(request, concept_id)


@login_required
def dish_detail_view(request, concept_id):
    """
    Show the most recent batch of generated dishes for a concept.
    If HTMX: return grid fragment. Else: return full page.
    """
    concept = get_object_or_404(
        models.Concept.objects.select_related("restaurant"),
        id=concept_id,
    )

    # Get the most recent ideation run for this concept
    latest_run = (
        models.IdeationRun.objects.filter(
            parent_concept=concept,
            type=models.IdeationRun.RunType.DISHES,
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        .order_by("-created_at")
        .first()
    )

    if latest_run:
        dish_queryset = (
            models.DishIdea.objects.filter(
                ideation_run=latest_run, is_deleted=False
            )
            .order_by("created_at")
        )
    else:
        dish_queryset = models.DishIdea.objects.none()

    dishes = list(dish_queryset)
    decorate_dishes_with_enhancements(dishes)

    favorite_ids = set()
    if request.user.is_authenticated and dishes:
        favorite_ids = set(
            models.FavoriteDish.objects.filter(
                user=request.user, dish__in=dishes
            ).values_list("dish_id", flat=True)
        )

    for dish in dishes:
        dish.is_favorited = dish.id in favorite_ids

    menu_options: List[dict] = []
    if request.user.is_authenticated:
        menu_queryset = models.MenuCollection.objects.filter(
            restaurant=concept.restaurant,
            restaurant__account__membership__user=request.user,
        ).order_by("created_at")
        menu_options = [{"id": str(menu.id), "name": menu.name} for menu in menu_queryset]

    template_name = "dishes/grid.html" if request.headers.get("HX-Request") == "true" else "dishes/page.html"

    logger.info(
        "Rendering dish detail view: concept=%s, run_id=%s, dish_count=%d, template=%s",
        concept.name,
        latest_run.id if latest_run else None,
        len(dishes),
        template_name,
    )

    concept_tags = concept.tags or []
    if isinstance(concept_tags, (tuple, set)):
        concept_tags = list(concept_tags)
    elif not isinstance(concept_tags, list):
        concept_tags = [concept_tags] if concept_tags else []

    concept_tags = [str(tag) for tag in concept_tags if str(tag).strip()]

    context = {
        "concept": concept,
        "concept_tags": concept_tags,
        "concept_reasoning": concept.reasoning or "",
        "dishes": dishes,
        "menu_options": menu_options,
        "menu_move_url": reverse("menu-item-move"),
        "menus_workspace_url": reverse("menus"),
        "dishes_generate_url": reverse("dishes-generate", args=[concept.id]),
    }

    return render(request, template_name, context)


def dish_favorite_view(request, dish_id):
    """Toggle favorite on a dish."""
    dish = get_object_or_404(
        models.DishIdea.objects.filter(is_deleted=False), id=dish_id
    )
    card_context = request.POST.get("context") or request.GET.get("context") or "grid"
    current_menu_id = (
        request.POST.get("current_menu_id")
        or request.GET.get("current_menu_id")
        or ""
    )
    menu_options: List[dict[str, str]] = []
    if request.user.is_authenticated:
        menu_queryset = models.MenuCollection.objects.filter(
            restaurant=dish.restaurant,
            restaurant__account__membership__user=request.user,
        ).order_by("created_at")
        menu_options = [
            {"id": str(menu.id), "name": menu.name} for menu in menu_queryset
        ]
    fav, created = models.FavoriteDish.objects.get_or_create(
        user=request.user, dish=dish, defaults={"favorited_at": timezone.now()}
    )
    if not created:
        fav.delete()
        favorited = False
        # Remove enhancement data when the dish is no longer favorited
        enhancements = list(
            models.Enhancement.objects.filter(dish=dish).select_related("image_asset")
        )
        asset_ids = [enh.image_asset_id for enh in enhancements if enh.image_asset_id]
        if enhancements:
            models.Enhancement.objects.filter(id__in=[enh.id for enh in enhancements]).delete()
        if asset_ids:
            models.Asset.objects.filter(id__in=asset_ids).delete()
    else:
        favorited = True
        ensure_dish_enhancement(dish, request.user)

    decorate_dishes_with_enhancements([dish])
    dish.is_favorited = favorited  # attach attribute for rendering

    if not favorited and card_context == "favorites":
        return HttpResponse("")

    html = render_to_string(
        "dishes/_card.html",
        {
            "dish": dish,
            "card_context": card_context,
            "menu_options": menu_options,
            "menu_move_url": reverse("menu-item-move"),
            "current_menu_id": current_menu_id,
            "menus_workspace_url": reverse("menus"),
        },
        request=request,
    )
    return HttpResponse(html)


@login_required
@require_POST
def dish_delete_view(request, dish_id):
    """Delete a dish and remove any associated enhancement assets."""

    dish = get_object_or_404(
        models.DishIdea.objects.select_related("restaurant").filter(
            is_deleted=False
        ),
        id=dish_id,
    )

    is_member = models.Membership.objects.filter(
        account=dish.restaurant.account, user=request.user
    ).exists()
    if not is_member:
        return HttpResponseForbidden()

    enhancements = list(
        models.Enhancement.objects.filter(dish=dish).select_related("image_asset")
    )
    asset_ids = [enh.image_asset_id for enh in enhancements if enh.image_asset_id]

    if enhancements:
        models.Enhancement.objects.filter(
            id__in=[enh.id for enh in enhancements]
        ).delete()

    models.FavoriteDish.objects.filter(dish=dish).delete()
    dish.is_deleted = True
    dish.save(update_fields=["is_deleted"])

    if asset_ids:
        models.Asset.objects.filter(id__in=asset_ids).delete()

    return HttpResponse("")


@login_required
@require_POST
def dish_variation_view(request, dish_id):
    """Return a freshly generated variation of a dish."""
    dish = get_object_or_404(
        models.DishIdea.objects.select_related(
            "restaurant", "parent_concept", "parent_dish", "ideation_run"
        ).filter(is_deleted=False),
        id=dish_id,
    )

    base_dish = dish.parent_dish or dish
    concept = dish.parent_concept
    restaurant = dish.restaurant

    slider_value, slider_temperature = _resolve_creativity_settings(restaurant)
    temperature_float = float(slider_temperature)
    if restaurant.active_menu_version:
        restaurant_menu = restaurant.active_menu_version.raw_markdown
    else:
        restaurant_menu = ""

    context_text = f"""
        Restaurant: {restaurant.name}, {restaurant.location_text}.  \n
        Description: {restaurant.description}. \n
        Current Restaurant Menu:  {restaurant_menu}
        About Services:  {restaurant.about_json}
    """
    context_text += (
        "\n        Creative direction slider: "
        f"{slider_value}/100 (0 = classic, 100 = highly inventive)."
    )

    deleted_dishes = list(
        models.DishIdea.objects.filter(restaurant=restaurant, is_deleted=True)
        .order_by("-created_at")
        .values_list("title", flat=True)[:15]
    )

    context_payload = {
        "context": context_text,
        "deleted_dishes": deleted_dishes,
        "classic_creative_slider": slider_value,
        "temperature": temperature_float,
    }

    existing_variations = list(
        models.DishIdea.objects.filter(parent_dish=base_dish, is_deleted=False)
        .order_by("created_at")
    )
    previous_titles = [base_dish.title] + [v.title for v in existing_variations]
    variation_number = len(existing_variations) + 1

    schema = {
        "name": "dish_variation",
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "ingredient_overlap": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "category_tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "title",
                "description",
                "ingredient_overlap",
                "category_tags",
            ],
            "additionalProperties": False,
        },
        "type": "json_schema",
        "strict": True,
    }

    variation_payload = {
        "context": context_payload,
        "original_dish": {
            "title": base_dish.title,
            "description": base_dish.description,
            "ingredient_overlap": getattr(base_dish, "ingredient_names", []),
            "category_tags": base_dish.category_tags,
        },
        "previous_variations": [
            {
                "title": v.title,
                "description": v.description,
            }
            for v in existing_variations
        ],
    }

    result = None
    max_attempts = 3

    for attempt in range(max_attempts):
        attempt_number = variation_number + attempt
        prompt = (
            "Generate a fresh culinary variation number {num} for the dish "
            "'{title}' that fits the restaurant context and concept. Avoid repeating "
            "any of these titles: {avoid}. Provide descriptive but concise copy."
        ).format(
            num=attempt_number,
            title=base_dish.title,
            avoid=", ".join(previous_titles) if previous_titles else "none",
        )

        try:
            if client:
                response = client.responses.create(
                    model="gpt-4.1",
                    input=[
                        {"role": "user", "content": prompt},
                        {"role": "user", "content": json.dumps(variation_payload, indent=2)},
                    ],
                    text={"format": schema},
                    temperature=temperature_float,
                )
                raw_text = response.output[0].content[0].text
                candidate = json.loads(raw_text)
            else:
                candidate = {
                    "title": f"{base_dish.title} Variation {attempt_number}",
                    "description": (
                        f"A playful take on {base_dish.title} inspired by variation {attempt_number}."
                    ),
                    "ingredient_overlap": list(
                        getattr(base_dish, "ingredient_names", [])[:3]
                    ),
                    "category_tags": list(base_dish.category_tags or []),
                }
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Dish variation generation failed: %s", exc)
            candidate = None

        if candidate and candidate.get("title") not in previous_titles:
            result = candidate
            break

        if candidate:
            previous_titles.append(candidate.get("title"))

    if not result:
        result = {
            "title": f"{base_dish.title} Variation {variation_number}",
            "description": (
                f"A creative riff on {base_dish.title} with new textures and flavors."
            ),
            "ingredient_overlap": list(getattr(base_dish, "ingredient_names", [])[:3]),
            "category_tags": list(base_dish.category_tags or []),
        }

    ingredient_overlap = result.get("ingredient_overlap") or []
    category_tags = result.get("category_tags") or []

    new_dish = models.DishIdea.objects.create(
        restaurant=restaurant,
        ideation_run=dish.ideation_run,
        parent_concept=concept,
        parent_dish=base_dish,
        title=result["title"],
        description=result["description"],
        ingredient_names=ingredient_overlap,
        category_tags=category_tags,
    )

    new_dish.is_favorited = False
    new_dish.ingredient_overlap = new_dish.ingredient_names

    decorate_dishes_with_enhancements([new_dish])

    menu_options: List[dict[str, str]] = []
    if request.user.is_authenticated:
        menu_queryset = models.MenuCollection.objects.filter(
            restaurant=restaurant,
            restaurant__account__membership__user=request.user,
        ).order_by("created_at")
        menu_options = [
            {"id": str(menu.id), "name": menu.name} for menu in menu_queryset
        ]
    current_menu_id = (
        request.POST.get("current_menu_id")
        or request.GET.get("current_menu_id")
        or ""
    )

    html = render_to_string(
        "dishes/_card.html",
        {
            "dish": new_dish,
            "card_context": "grid",
            "menu_options": menu_options,
            "menu_move_url": reverse("menu-item-move"),
            "current_menu_id": current_menu_id,
            "menus_workspace_url": reverse("menus"),
        },
        request=request,
    )
    return HttpResponse(html)


@login_required
def favorites_view(request):
    """Render favorites dashboard."""
    restaurant = (
        models.Restaurant.objects.filter(account__membership__user=request.user)
        .select_related("account")
        .first()
    )
    favorite_concepts = list(
        models.FavoriteConcept.objects.filter(user=request.user)
        .select_related("concept", "concept__restaurant")
        .order_by("-favorited_at")
    )
    for favorite in favorite_concepts:
        if favorite.concept:
            favorite.concept.is_favorited_for_user = True
    favorite_dishes = list(
        models.FavoriteDish.objects.filter(user=request.user)
        .select_related("dish__parent_concept", "dish__restaurant")
        .order_by("-favorited_at")
    )

    menus = []
    menu_dishes = []
    if restaurant:
        menus = list(
            models.MenuCollection.objects.filter(restaurant=restaurant)
            .prefetch_related(
                Prefetch(
                    "menuitem_set",
                    queryset=models.MenuItem.objects.select_related(
                        "dish",
                        "dish__parent_concept",
                        "dish__restaurant",
                    ).order_by("position", "created_at"),
                )
            )
            .order_by("created_at")
        )
        for menu in menus:
            items = list(menu.menuitem_set.all())
            menu.menu_items = items
            for item in items:
                menu_dishes.append(item.dish)

    all_dishes = [fav.dish for fav in favorite_dishes] + menu_dishes
    decorate_dishes_with_enhancements(all_dishes)
    for dish in all_dishes:
        dish.is_favorited = True

    menu_dish_ids = {item.dish_id for menu in menus for item in getattr(menu, "menu_items", [])}
    uncategorized_favorites = [
        fav for fav in favorite_dishes if fav.dish_id not in menu_dish_ids
    ]

    menus_payload = [
        {
            "id": str(menu.id),
            "name": menu.name,
        }
        for menu in menus
    ]

    ctx = {
        "restaurant": restaurant,
        "favorite_concepts": favorite_concepts,
        "favorite_dishes": favorite_dishes,
        "menus": menus,
        "uncategorized_favorites": uncategorized_favorites,
        "menu_options": menus_payload,
        "menu_move_url": reverse("menu-item-move"),
        "menus_workspace_url": reverse("menus"),
    }
    return render(request, "favorites/dashboard.html", ctx)


@login_required
@require_POST
def favorite_remove_view(request, type, id):
    """Remove a favorite concept or dish."""
    if type == "concept":
        favorite = get_object_or_404(
            models.FavoriteConcept, user=request.user, concept_id=id
        )
        concept = favorite.concept
        favorite.delete()
        if concept.sketch_image_url:
            concept.sketch_image_url = None
            concept.save(update_fields=["sketch_image_url"])
    else:
        models.FavoriteDish.objects.filter(user=request.user, dish_id=id).delete()
    if request.headers.get("HX-Request"):
        return HttpResponse("")
    return JsonResponse({"removed": True})


@login_required
@require_POST
def menu_collection_create_view(request):
    """Create a new menu collection."""
    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "name_required"}, status=400)
    restaurant = (
        models.Restaurant.objects.filter(account__membership__user=request.user)
        .select_related("account")
        .first()
    )
    if not restaurant:
        return JsonResponse({"error": "restaurant_missing"}, status=400)
    menu = models.MenuCollection.objects.create(
        restaurant=restaurant, created_by_user=request.user, name=name
    )
    return JsonResponse({"id": str(menu.id), "name": menu.name})


@login_required
@require_POST
def menu_item_add_view(request, dish_id, collection_id):
    """Add a dish to a menu collection."""
    dish = get_object_or_404(
        models.DishIdea.objects.filter(is_deleted=False), id=dish_id
    )
    menu = get_object_or_404(
        models.MenuCollection,
        id=collection_id,
        restaurant__account__membership__user=request.user,
    )
    next_position = (
        models.MenuItem.objects.filter(menu=menu).aggregate(Max("position"))["position__max"]
        or 0
    )
    models.MenuItem.objects.get_or_create(
        menu=menu, dish=dish, defaults={"position": next_position + 1}
    )
    return JsonResponse({"added": True})


@login_required
@require_POST
def menu_collection_update_view(request, collection_id):
    """Rename a menu collection."""
    menu = get_object_or_404(
        models.MenuCollection,
        id=collection_id,
        restaurant__account__membership__user=request.user,
    )
    new_name = (request.POST.get("name") or "").strip() or "Menu"
    menu.name = new_name
    menu.save(update_fields=["name"])
    return JsonResponse({"id": str(menu.id), "name": menu.name})


@login_required
@require_POST
def menu_collection_delete_view(request, collection_id):
    """Delete a menu collection."""
    menu = get_object_or_404(
        models.MenuCollection,
        id=collection_id,
        restaurant__account__membership__user=request.user,
    )
    menu.delete()
    return JsonResponse({"deleted": True})


@login_required
@require_POST
def menu_item_move_view(request):
    """Move a dish between menu collections or into uncategorized."""
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid_json"}, status=400)

    dish_id = payload.get("dish_id")
    target_menu_id = payload.get("target_menu_id") or None
    source_menu_id = payload.get("source_menu_id") or None

    if not dish_id:
        return JsonResponse({"error": "dish_required"}, status=400)

    restaurant = (
        models.Restaurant.objects.filter(account__membership__user=request.user)
        .select_related("account")
        .first()
    )
    if not restaurant:
        return JsonResponse({"error": "restaurant_missing"}, status=400)

    dish = get_object_or_404(
        models.DishIdea.objects.filter(is_deleted=False), id=dish_id
    )

    def _remove_from_menu(menu_id):
        if not menu_id:
            return
        models.MenuItem.objects.filter(
            menu_id=menu_id,
            dish=dish,
            menu__restaurant=restaurant,
        ).delete()

    # Remove from the source menu if provided
    _remove_from_menu(source_menu_id)

    created = False
    if target_menu_id:
        menu = get_object_or_404(
            models.MenuCollection,
            id=target_menu_id,
            restaurant=restaurant,
        )
        next_position = (
            models.MenuItem.objects.filter(menu=menu).aggregate(Max("position"))["position__max"]
            or 0
        )
        menu_item, created = models.MenuItem.objects.get_or_create(
            menu=menu,
            dish=dish,
            defaults={"position": next_position + 1},
        )
        if not created:
            # Ensure position is at end when moving existing record
            menu_item.position = next_position + 1
            menu_item.save(update_fields=["position"])

    return JsonResponse({"moved": True, "created": created})


@login_required
@require_http_methods(["POST"])
def menu_collaboration_manage_view(request, collection_id):
    """Create, update, or disable collaboration links for a menu."""

    menu = get_object_or_404(
        models.MenuCollection,
        id=collection_id,
        restaurant__account__membership__user=request.user,
    )

    action = (request.POST.get("action") or "").strip().lower()
    expires_days_raw = request.POST.get("expires_in_days") or "7"
    try:
        expires_in_days = max(1, min(90, int(expires_days_raw)))
    except ValueError:
        expires_in_days = 7
    passcode = (request.POST.get("passcode") or "").strip() or None

    existing = (
        models.CollaborationLink.objects.filter(menu=menu, is_active=True)
        .order_by("-created_at")
        .first()
    )

    if action == "disable":
        models.CollaborationLink.objects.filter(menu=menu, is_active=True).update(
            is_active=False
        )
        return redirect("menus")

    if action == "expire" and existing:
        existing.expires_at = timezone.now()
        existing.save(update_fields=["expires_at"])
        return redirect("menus")

    if action not in {"enable", "regenerate"}:
        return JsonResponse({"error": "unknown_action"}, status=400)

    if existing:
        existing.is_active = False
        existing.save(update_fields=["is_active"])
        if passcode is None:
            passcode = existing.passcode

    expires_at = timezone.now() + datetime.timedelta(days=expires_in_days)
    models.CollaborationLink.objects.create(
        menu=menu,
        expires_at=expires_at,
        passcode=passcode,
    )
    return redirect("menus")


def _collaboration_session_keys(link_id: uuid.UUID) -> tuple[str, str]:
    """Return keys used for session storage for collaboration links."""

    access_key = f"collab_access_{link_id}"
    visit_key = f"collab_visit_{link_id}"
    return access_key, visit_key


def _format_feedback_activity(feedback: models.Feedback) -> str:
    """Create a readable description of a feedback item."""

    dish_name = getattr(getattr(feedback, "dish", None), "title", "")
    anon_label = feedback.anon_label

    if feedback.type == models.Feedback.Type.THUMBS_UP:
        return f"{anon_label} gave 👍 to {dish_name or 'the menu'}."
    if feedback.type == models.Feedback.Type.THUMBS_DOWN:
        return f"{anon_label} gave 👎 to {dish_name or 'the menu'}."
    if feedback.type == models.Feedback.Type.COMMENT:
        text = feedback.payload.get("comment", "")
        target = dish_name or "the menu"
        return f"{anon_label} commented on {target}: \"{text}\""
    if feedback.type == models.Feedback.Type.EDIT_SUGGESTION:
        target = dish_name or "the menu"
        title = feedback.payload.get("title") or "Edit suggestion"
        return f"{anon_label} suggested an edit for {target}: {title}."
    if feedback.type == models.Feedback.Type.NEW_IDEA:
        title = feedback.payload.get("title") or "New idea"
        return f"{anon_label} suggested a new dish: {title}."
    if feedback.type == models.Feedback.Type.REORDER:
        return f"{anon_label} proposed a new dish order."
    return f"{anon_label} shared feedback."


@require_http_methods(["GET", "POST"])
def collaboration_dashboard_view(request, link_id):
    """Public dashboard for staff collaboration."""

    link = get_object_or_404(models.CollaborationLink, id=link_id)
    if not link.is_active or link.is_expired():
        return render(
            request,
            "collaboration/link_expired.html",
            {"link": link},
            status=410,
        )

    access_key, visit_key = _collaboration_session_keys(link.id)
    passcode_valid = not link.passcode or request.session.get(access_key)

    if link.passcode and request.method == "POST" and not passcode_valid:
        submitted = (request.POST.get("passcode") or "").strip()
        if submitted and submitted == link.passcode:
            request.session[access_key] = True
            return redirect("collaboration-dashboard", link_id=link.id)
        return render(
            request,
            "collaboration/passcode.html",
            {"link": link, "error": True},
            status=403,
        )

    if not passcode_valid:
        return render(request, "collaboration/passcode.html", {"link": link})

    if not request.session.get(visit_key):
        link.mark_accessed()
        request.session[visit_key] = True

    menu = link.menu
    items = list(
        menu.menuitem_set.select_related(
            "dish",
            "dish__parent_concept",
        ).order_by("position", "created_at")
    )
    menu.menu_items = items

    thumb_counts: dict[uuid.UUID, dict[str, int]] = {}
    feedback_items = list(
        link.feedback.select_related("dish").order_by("-created_at")[:50]
    )
    for feedback in feedback_items:
        if feedback.dish_id:
            thumb_counts.setdefault(feedback.dish_id, {"up": 0, "down": 0})
            if feedback.type == models.Feedback.Type.THUMBS_UP:
                thumb_counts[feedback.dish_id]["up"] += 1
            elif feedback.type == models.Feedback.Type.THUMBS_DOWN:
                thumb_counts[feedback.dish_id]["down"] += 1

    for item in items:
        thumb_counts.setdefault(item.dish_id, {"up": 0, "down": 0})

    activity_feed = [
        {
            "message": _format_feedback_activity(feedback),
            "created_at": feedback.created_at,
        }
        for feedback in feedback_items
    ]

    anon_session_key = f"collab_anon_{link.id}"
    anon_id = request.session.get(anon_session_key)
    if not anon_id:
        anon_id = uuid.uuid4().hex[:8]
        request.session[anon_session_key] = anon_id

    ctx = {
        "link": link,
        "menu": menu,
        "items": items,
        "thumb_counts": thumb_counts,
        "activity_feed": activity_feed,
        "anon_id": anon_id,
        "models": models,
    }
    return render(request, "collaboration/dashboard.html", ctx)


@require_POST
def collaboration_feedback_submit_view(request, link_id):
    """Store feedback from the public collaboration dashboard."""

    link = get_object_or_404(models.CollaborationLink, id=link_id, is_active=True)
    if link.is_expired():
        return HttpResponseForbidden("link_expired")

    access_key, _ = _collaboration_session_keys(link.id)
    if link.passcode and not request.session.get(access_key):
        return HttpResponseForbidden("passcode_required")

    feedback_type = (request.POST.get("type") or "").strip()
    if feedback_type not in {choice for choice, _ in models.Feedback.Type.choices}:
        return JsonResponse({"error": "invalid_type"}, status=400)

    anon_id = (request.POST.get("anon_id") or "").strip()
    if not anon_id:
        anon_id = uuid.uuid4().hex[:8]

    menu = link.menu
    dish = None
    dish_id = request.POST.get("dish_id")
    if dish_id:
        dish = (
            models.DishIdea.objects.filter(id=dish_id, menuitem__menu=menu)
            .distinct()
            .first()
        )
        if not dish:
            return JsonResponse({"error": "invalid_dish"}, status=400)

    payload: dict[str, Any]
    payload = {}

    if feedback_type in {
        models.Feedback.Type.THUMBS_UP,
        models.Feedback.Type.THUMBS_DOWN,
    }:
        payload = {}
    elif feedback_type == models.Feedback.Type.COMMENT:
        comment = (request.POST.get("comment") or "").strip()
        if not comment:
            return JsonResponse({"error": "comment_required"}, status=400)
        payload = {"comment": comment}
    elif feedback_type == models.Feedback.Type.EDIT_SUGGESTION:
        title = (request.POST.get("title") or "").strip()
        description = (request.POST.get("description") or "").strip()
        category = (request.POST.get("category") or "").strip()
        if not title and not description:
            return JsonResponse({"error": "edit_required"}, status=400)
        payload = {
            "title": title,
            "description": description,
            "category": category,
        }
    elif feedback_type == models.Feedback.Type.REORDER:
        order_raw = request.POST.get("order") or "[]"
        try:
            order = json.loads(order_raw)
        except json.JSONDecodeError:
            return JsonResponse({"error": "invalid_order"}, status=400)
        if not isinstance(order, list):
            return JsonResponse({"error": "invalid_order"}, status=400)
        payload = {"order": order}
    elif feedback_type == models.Feedback.Type.NEW_IDEA:
        title = (request.POST.get("title") or "").strip()
        notes = (request.POST.get("notes") or "").strip()
        payload = {"title": title, "notes": notes}
    else:  # pragma: no cover - safeguard
        payload = {}

    feedback = models.Feedback.objects.create(
        menu=menu,
        dish=dish,
        link=link,
        type=feedback_type,
        payload=payload,
        anon_id=anon_id,
    )
    models.FeedbackAction.objects.create(feedback=feedback)

    return redirect("collaboration-dashboard", link_id=link.id)


@login_required
def menu_feedback_review_view(request, collection_id):
    """Show collaboration feedback for a menu to the chef."""

    menu = get_object_or_404(
        models.MenuCollection,
        id=collection_id,
        restaurant__account__membership__user=request.user,
    )

    feedback_queryset = (
        models.Feedback.objects.filter(menu=menu)
        .select_related("dish", "link", "action")
        .order_by("-created_at")
    )

    pending_feedback: List[dict[str, Any]] = []
    history_feedback: List[dict[str, Any]] = []
    dish_titles = {
        str(dish.id): dish.title
        for dish in models.DishIdea.objects.filter(menuitem__menu=menu).distinct()
    }
    for item in feedback_queryset:
        action = getattr(item, "action", None)
        entry = {
            "feedback": item,
            "message": _format_feedback_activity(item),
            "status": action.status if action else models.FeedbackAction.Status.PENDING,
        }
        if action and action.status != models.FeedbackAction.Status.PENDING:
            history_feedback.append(entry)
        else:
            pending_feedback.append(entry)

    ctx = {
        "menu": menu,
        "pending_feedback": pending_feedback,
        "history_feedback": history_feedback,
        "models": models,
        "dish_titles": dish_titles,
    }
    return render(request, "menus/collaboration_review.html", ctx)


@login_required
@require_POST
def menu_feedback_action_view(request, feedback_id):
    """Approve or reject a feedback item."""

    feedback = get_object_or_404(
        models.Feedback.objects.select_related("menu", "menu__restaurant"),
        id=feedback_id,
        menu__restaurant__account__membership__user=request.user,
    )

    status = (request.POST.get("status") or "").strip().lower()
    if status not in {
        models.FeedbackAction.Status.APPROVED,
        models.FeedbackAction.Status.REJECTED,
    }:
        return JsonResponse({"error": "invalid_status"}, status=400)

    notes = (request.POST.get("notes") or "").strip()
    action = getattr(feedback, "action", None)
    if not action:
        action = models.FeedbackAction.objects.create(feedback=feedback)
    action.mark(status, user=request.user, notes=notes)

    return redirect("menu-feedback-review", collection_id=feedback.menu_id)


@login_required
def settings_view(request):
    restaurant = (
        models.Restaurant.objects.filter(account__membership__user=request.user)
        .select_related("active_menu_version", "restaurantsettings")
        .first()
    )
    ingredients = list(
        models.Ingredient.objects.filter(restaurant=restaurant).values_list("name", flat=True)
    )
    prefs = getattr(request.user, "notificationpref", None)
    active_menu = restaurant.active_menu_version if restaurant else None
    return render(request, "settings/main.html", {
        "restaurant": restaurant,
        "ingredients": ingredients,
        "prefs": prefs,
        "restaurant_settings": getattr(restaurant, "restaurantsettings", None),
        "active_menu_version": active_menu,
    })


@login_required
@require_POST
def update_restaurant_info(request):
    restaurant = models.Restaurant.objects.filter(account__membership__user=request.user).first()
    if not restaurant:
        return redirect("settings")

    form_type = request.POST.get("form_type") or "urls"

    if form_type == "content":
        menu_text = (request.POST.get("menu_text") or "").strip()
        menu_pdf = request.FILES.get("menu_pdf")
        if menu_text or menu_pdf:
            _process_menu_submission(restaurant, None, menu_text, menu_pdf)
        return redirect("settings")

    submitted_values = request.POST.getlist("menu_urls")
    if submitted_values:
        combined = []
        for value in submitted_values:
            if not value:
                continue
            combined.append(value)
        if combined:
            normalized = "\n".join(combined).replace("\r", "\n").replace(",", "\n")
            urls = [line.strip() for line in normalized.split("\n") if line.strip()]
        else:
            urls = []
    else:
        raw_urls = request.POST.get("menu_urls")
        if raw_urls is None:
            menu_url = (request.POST.get("menu_url") or "").strip()
            urls = [menu_url] if menu_url else []
        else:
            normalized = raw_urls.replace("\r", "\n").replace(",", "\n")
            urls = [line.strip() for line in normalized.split("\n") if line.strip()]

    restaurant.set_menu_urls(urls)
    restaurant.save(update_fields=["menu_urls", "primary_menu_url"])

    ingredient_names = [
        name.strip()
        for name in (request.POST.get("ingredients", "") or "").split(",")
        if name.strip()
    ]
    for name in ingredient_names:
        models.Ingredient.objects.get_or_create(restaurant=restaurant, name=name)

    return redirect("settings")


@require_POST
def rescrape_restaurant(request, restaurant_id):
    restaurant = get_object_or_404(models.Restaurant, id=restaurant_id)
    payload = models.OutscraperPayload.objects.create(
        restaurant=restaurant,
        status=models.OutscraperPayload.Status.QUEUED,
        request_params={"query": restaurant.name, "limit": 1, "async": "false"},
    )
    run_outscraper_search.delay(str(payload.id))
    return HttpResponse("rescrape_complete", content_type="text/plain")


@require_POST
def update_creativity(request, restaurant_id):
    restaurant = get_object_or_404(models.Restaurant, id=restaurant_id)
    slider_value = request.POST.get("classic_creative_slider")
    if slider_value is not None:
        restaurant.restaurantsettings.classic_creative_slider = int(slider_value)
        restaurant.restaurantsettings.save(update_fields=["classic_creative_slider"])
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def rescrape_menu(request, restaurant_id):
    restaurant = get_object_or_404(models.Restaurant, id=restaurant_id)
    logger.info("Rescrape requested by user=%s for restaurant=%s (%s)",
                request.user.id, restaurant.name, restaurant.id)

    if not restaurant.primary_menu_url:
        logger.warning("Restaurant %s (%s) has no primary_menu_url set. Cannot rescrape.",
                       restaurant.name, restaurant.id)
        return JsonResponse({"error": "missing_menu_url"}, status=400)

    mv = models.MenuVersion.objects.create(
        restaurant=restaurant,
        source_url=restaurant.primary_menu_url,
        source_kind=models.MenuVersion.SourceKind.URL_SCRAPE,
        raw_markdown="",
        status=models.MenuVersion.Status.QUEUED,
    )
    logger.info("Created MenuVersion id=%s for restaurant=%s (%s)",
                mv.id, restaurant.name, restaurant.id)

    # Queue Celery task
    scrape_menu.delay(str(mv.id))
    logger.info("Dispatched scrape_menu task for MenuVersion id=%s", mv.id)

    return JsonResponse({"rescrape_complete": True})



@login_required
@require_POST
def update_notifications(request):
    prefs, _ = models.NotificationPref.objects.get_or_create(user=request.user)
    prefs.on_background_complete_email = "on_background_complete_email" in request.POST
    prefs.on_new_menu_version_email = "on_new_menu_version_email" in request.POST
    prefs.save()
    return redirect("settings")



@login_required
def billing_view(request):
    """Show billing page."""

    membership = (
        models.Membership.objects.filter(user=request.user)
        .select_related("account")
        .first()
    )
    account = membership.account if membership else None
    subscription = _latest_subscription_for_account(account) if account else None
    plan = _get_default_plan() if account else None
    trial_days = getattr(settings, "STRIPE_TRIAL_DAYS", 14)
    trial_end = subscription.current_period_end if subscription else None
    trial_days_remaining = None
    if subscription and subscription.status == models.Subscription.Status.TRIALING:
        remaining = subscription.current_period_end - timezone.now()
        trial_days_remaining = max(remaining.days, 0)

    context = {
        "plan": plan,
        "subscription": subscription,
        "trial_days": trial_days,
        "trial_end": trial_end,
        "trial_days_remaining": trial_days_remaining,
        "show_start_trial": bool(account)
        and (
            not subscription
            or subscription.status == models.Subscription.Status.CANCELED
        ),
    }
    return render(request, "billing/main.html", context)


@login_required
@require_POST
def billing_upgrade_view(request):
    """Create a Stripe Checkout session to start the trial subscription."""

    membership = (
        models.Membership.objects.filter(user=request.user)
        .select_related("account")
        .first()
    )
    price_id = getattr(settings, "STRIPE_PRICE_ID", "")
    if not membership or not price_id:
        return redirect("billing")

    account = membership.account
    _ensure_stripe_api_key()
    metadata = {"account_id": str(account.id)}
    next_path = request.POST.get("next") or reverse("billing")
    if not isinstance(next_path, str) or not next_path.startswith("/"):
        next_path = reverse("billing")
    success_url = request.build_absolute_uri(next_path)
    cancel_url = success_url

    session_kwargs = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "subscription_data": {
            "trial_period_days": getattr(settings, "STRIPE_TRIAL_DAYS", 14),
            "metadata": metadata,
        },
        "metadata": metadata,
    }

    if account.stripe_customer_id:
        session_kwargs["customer"] = account.stripe_customer_id
    else:
        session_kwargs["customer_email"] = request.user.email

    try:
        checkout_session = stripe.checkout.Session.create(**session_kwargs)
    except stripe.error.StripeError:
        logger.exception("Unable to create Stripe Checkout session", exc_info=True)
        return redirect("billing")

    return redirect(checkout_session.url)


@login_required
@require_POST
def billing_cancel_view(request):
    """Cancel subscription at period end."""

    membership = (
        models.Membership.objects.filter(user=request.user)
        .select_related("account")
        .first()
    )
    if not membership:
        return redirect("billing")

    account = membership.account
    subscription = _latest_subscription_for_account(account)
    if not subscription:
        return redirect("billing")

    if (
        subscription.provider == models.Subscription.Provider.STRIPE
        and getattr(settings, "STRIPE_SECRET_KEY", "")
    ):
        _ensure_stripe_api_key()
        try:
            stripe.Subscription.modify(
                subscription.provider_sub_id, cancel_at_period_end=True
            )
        except stripe.error.StripeError:
            logger.exception("Unable to cancel Stripe subscription", exc_info=True)

    if not subscription.cancel_at_period_end:
        subscription.cancel_at_period_end = True
        subscription.save(update_fields=["cancel_at_period_end"])

    return redirect("billing")


@csrf_exempt
@require_POST
def stripe_webhook_view(request):
    """Handle Stripe webhook callbacks for subscriptions."""

    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")

    if secret:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, secret)
        except ValueError:
            return HttpResponseBadRequest("invalid_payload")
        except stripe.error.SignatureVerificationError:
            return HttpResponseForbidden("invalid_signature")
    else:
        try:
            event = json.loads(payload or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("invalid_json")

    event_type = event.get("type")
    data_object = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        subscription_id = data_object.get("subscription")
        if subscription_id:
            _ensure_stripe_api_key()
            try:
                subscription = stripe.Subscription.retrieve(subscription_id)
            except stripe.error.StripeError:
                logger.exception(
                    "Unable to retrieve subscription %s from Stripe", subscription_id
                )
            else:
                _sync_subscription(subscription)
    elif event_type in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }:
        _sync_subscription(data_object)

    return HttpResponse(status=200)


def job_status_view(request, job_id):
    """Return job status."""
    job = get_object_or_404(models.Job, id=job_id)
    return JsonResponse({"status": job.status})


def notification_list_view(request):
    """Render notification list."""
    notes = models.Notification.objects.filter(user=request.user)
    return render(request, "notifications/list.html", {"notifications": notes})

def restaurant_status(request, restaurant_id):
    """HTMX endpoint that returns current status widget."""
    restaurant = get_object_or_404(
        models.Restaurant.objects.select_related("restaurantsettings"),
        id=restaurant_id,
    )
    payload = (
        models.OutscraperPayload.objects.filter(restaurant=restaurant)
        .order_by("-created_at")
        .first()
    )
    context = {
        "restaurant": restaurant,
        "menu_version": restaurant.active_menu_version,
        "payload": payload,
        "restaurant_settings": getattr(restaurant, "restaurantsettings", None),
    }
    return render(request, "_partials/restaurant_status.html", context)


def show_menu_modal(request, restaurant_id):
    restaurant = get_object_or_404(models.Restaurant, id=restaurant_id)
    return render(request, "_partials/menu_modal.html", {"restaurant": restaurant})


def _process_menu_submission(
    restaurant: models.Restaurant,
    menu_url: Optional[str],
    menu_text: Optional[str],
    menu_pdf,
):
    """Create a menu version from URL, pasted text, or uploaded PDF."""

    if menu_url:
        restaurant.add_menu_url(menu_url)
        mv = models.MenuVersion.objects.create(
            restaurant=restaurant,
            source_url=menu_url,
            source_kind=models.MenuVersion.SourceKind.URL_SCRAPE,
            raw_markdown="",
            status=models.MenuVersion.Status.QUEUED,
        )
        restaurant.active_menu_version = mv
        restaurant.save(
            update_fields=["menu_urls", "primary_menu_url", "active_menu_version"]
        )
        transaction.on_commit(lambda mv_id=str(mv.id): scrape_menu.delay(mv_id))
        return mv

    if menu_text:
        mv = models.MenuVersion.objects.create(
            restaurant=restaurant,
            source_kind=models.MenuVersion.SourceKind.PASTED_TEXT,
            raw_markdown=menu_text,
            status=models.MenuVersion.Status.SUCCEEDED,
        )
        restaurant.active_menu_version = mv
        restaurant.save(update_fields=["active_menu_version"])
        return mv

    if menu_pdf:
        path = default_storage.save(
            f"menus/{restaurant.id}/{menu_pdf.name}",
            ContentFile(menu_pdf.read()),
        )
        mv = models.MenuVersion.objects.create(
            restaurant=restaurant,
            source_url=path,
            source_kind=models.MenuVersion.SourceKind.IMAGE_OCR,
            raw_markdown="",
            status=models.MenuVersion.Status.QUEUED,
        )
        restaurant.active_menu_version = mv
        restaurant.save(update_fields=["active_menu_version"])
        transaction.on_commit(
            lambda mv_id=str(mv.id), storage_path=path: parse_pdf_menu.delay(
                mv_id, storage_path
            )
        )
        return mv

    return None


def upload_menu(request, restaurant_id):
    restaurant = get_object_or_404(models.Restaurant, id=restaurant_id)

    if request.method == "POST":
        submitted_urls = request.POST.getlist("menu_url")
        menu_urls = [url.strip() for url in submitted_urls if url and url.strip()]
        if submitted_urls:
            restaurant.set_menu_urls(menu_urls)
            restaurant.save(update_fields=["menu_urls", "primary_menu_url"])

        menu_text = (request.POST.get("menu_text") or "").strip()
        menu_pdf = request.FILES.get("menu_pdf")

        menu_url = None
        if not menu_text and not menu_pdf and menu_urls:
            menu_url = menu_urls[0]

        _process_menu_submission(restaurant, menu_url, menu_text, menu_pdf)

    return restaurant_status(request, restaurant_id)
