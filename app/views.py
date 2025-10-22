"""Application views."""

import json, logging, os, uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, List, Optional

from django import forms
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import RequestDataTooBig
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.mail import EmailMultiAlternatives
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
from django.apps import apps
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.core.cache import cache
import hashlib
import requests
from openai import OpenAI
from . import models
from dotenv import load_dotenv
load_dotenv()
_openai_api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=_openai_api_key) if _openai_api_key else None
import datetime
import stripe
from . import signup_service
from .outscraper import queue_outscraper_payload
from .billing import create_checkout_session, _see_other
from .tasks import *
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from app.models import Onboarding
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

    try:
        settings = restaurant.restaurantsettings
    except models.RestaurantSettings.DoesNotExist:
        settings = None
    if not settings:
        settings, _ = models.RestaurantSettings.objects.get_or_create(
            restaurant=restaurant
        )

    slider = int(getattr(settings, "classic_creative_slider", 50) or 50)
    slider = max(0, min(100, slider))
    temperature = Decimal("0.1") + Decimal(slider) * Decimal("0.008")
    temperature = temperature.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return slider, temperature


def _sanitize_slider_value(raw_value: Any) -> Optional[int]:
    """Return a sanitized slider integer if the raw value is valid."""

    if raw_value in (None, ""):
        return None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None
    return max(0, min(100, value))


def _persist_slider_value(
    restaurant: "models.Restaurant", slider_value: int
) -> "models.RestaurantSettings":
    """Ensure the restaurant's settings reflect the provided slider value."""

    settings, _ = models.RestaurantSettings.objects.get_or_create(
        restaurant=restaurant
    )
    if settings.classic_creative_slider != slider_value:
        settings.classic_creative_slider = slider_value
        settings.save(update_fields=["classic_creative_slider"])
    setattr(restaurant, "restaurantsettings", settings)
    return settings

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


def _article_model():
    """Return the marketing article model if the articles app is installed."""
    try:
        return apps.get_model("articles", "Article")
    except LookupError:
        return None


def _get_published_articles(limit: int | None = None, fields: Iterable[str] | None = None) -> List[Any]:
    """Fetch published articles ordered by recency."""
    article_model = _article_model()
    if article_model is None:
        return []

    queryset = article_model.objects.filter(status="published", published_at__isnull=False).order_by("-published_at")
    if fields:
        queryset = queryset.only(*fields)
    if limit:
        queryset = queryset[:limit]
    return list(queryset)


def _footer_articles(limit: int = 4) -> List[Any]:
    """Return a small set of published articles for footer links."""

    cache_key = f"footer-articles:{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    articles = _get_published_articles(
        limit=limit,
        fields=("title", "slug", "published_at"),
    )
    cache.set(cache_key, articles, timeout=DEFAULT_CACHE_TIMEOUT)
    return articles


def _verify_recaptcha(token: str, remote_ip: str | None = None) -> bool:
    """Verify a reCAPTCHA token and return True when allowed."""

    secret_key = getattr(settings, "RECAPTCHA_SECRET_KEY", None)
    if not secret_key:
        logger.warning("RECAPTCHA_SECRET_KEY not configured, skipping verification")
        return True
    if not token:
        logger.warning("No reCAPTCHA token provided")
        return False

    payload = {"secret": secret_key, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        response = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data=payload,
            timeout=5,
        )
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("reCAPTCHA verification failed", exc_info=True)
        return False

    result = response.json()
    success = result.get("success", False)
    score = result.get("score", 0)
    if not success or score < 0.5:
        logger.warning("reCAPTCHA verification did not pass", extra={"score": score})
        return False
    return True


def _stripe_timestamp(value: Optional[int]) -> datetime.datetime:
    """Convert a Stripe timestamp into an aware datetime."""

    if not value:
        return timezone.now()
    return datetime.datetime.fromtimestamp(value, tz=datetime.timezone.utc)


def _sync_subscription(subscription_data: dict) -> Optional[models.Account]:
    """Create or update a subscription based on Stripe payload."""

    sub_id = subscription_data.get("id")
    if not sub_id:
        return None

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
        return None

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

    subscription_obj, _ = models.Subscription.objects.update_or_create(
        account=account,
        provider=models.Subscription.Provider.STRIPE,
        provider_sub_id=sub_id,
        defaults=defaults,
    )
    if subscription_obj.account_id != account.id:
        subscription_obj.account = account
        subscription_obj.save(update_fields=["account"])
    return account


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


def _record_unfavorited_concept(session, concept: models.Concept) -> bool:
    """Persist and surface a concept the user removed from favorites.

    Returns True when the concept unfavorite state changed.
    """

    if not concept:
        return False

    changed = False
    if not concept.is_unfavorite:
        concept.is_unfavorite = True
        changed = True

    concept.is_unfavorited_for_user = True

    name = (concept.name or "").strip()
    if not name:
        return changed

    _extend_session_list(session, "disliked_concepts", [name])
    disliked = _get_session_list(session, "disliked_concepts")
    if len(disliked) > 30:
        session["disliked_concepts"] = disliked[-30:]
        session.modified = True
    return changed


def _get_unfavorited_concept_names(restaurant: Optional[models.Restaurant], limit: int) -> List[str]:
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
        concept.is_unfavorited_for_user = concept.is_unfavorite
        concept.has_dishes = bool(dish) or getattr(concept, "has_dishes", False)
        concept.favorited_at = concept_favorite.favorited_at
        restaurant = getattr(concept, "restaurant", None)

    except Exception:  # pragma: no cover - defensive to keep landing safe
        logger.exception("Failed to load demo favorites for home view")

    return concept, concept_favorite, dish, dish_favorite, restaurant

from app import llm

def dish_grid(request, concept_name: str):
    """Render a 3x3 grid of dishes for a concept."""
    dishes = llm.generate_dishes(concept_name)
    ctx = {"concept": concept_name, "dishes": dishes}
    return render(request, "app/dish_grid.html", ctx)


class NewsletterSignupForm(forms.Form):
    """Simple email capture used on the marketing landing page."""

    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                "placeholder": "Enter your email",
                "autocomplete": "email",
                "class": "w-full sm:flex-1 rounded-full border border-indigo-500/25 bg-slate-950/70 px-5 py-3 text-base text-slate-100 placeholder-slate-500 transition focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-400/40",
            }
        ),
        label="",
    )


def home_view(request):
    """Landing page with signup/login links."""

    subscription_status = None
    form = NewsletterSignupForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            email = form.cleaned_data["email"].strip().lower()

            try:
                subscriber, created = models.NewsletterSubscriber.objects.get_or_create(
                    email=email,
                    defaults={"source": "new_home"},
                )
            except IntegrityError:
                # In the unlikely event of a race, fall back to fetch/update.
                subscriber = models.NewsletterSubscriber.objects.filter(email=email).first()
                created = False

            if subscriber and not subscriber.source:
                subscriber.source = "new_home"
                subscriber.save(update_fields=["source"])

            subscription_status = "created" if created else "exists"
            form = NewsletterSignupForm()
        else:
            subscription_status = "invalid"

    context = {
        "newsletter_form": form,
        "subscription_status": subscription_status,
        "latest_articles": _get_published_articles(
            limit=3,
            fields=("title", "summary", "slug", "seo_description", "published_at", "og_image_url"),
        ),
    }

    return render(request, "new_home.html", context)


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

def data_security_view(request):
    """Public data security page."""

    context = {"footer_articles": _footer_articles()}
    return render(request, "data_security.html", context)




@login_required
def setup_view(request):
    """Render the setup placeholder page shown after checkout."""

    session_id = request.GET.get("session_id", "")
    onboarding_id = request.GET.get("onboarding_id", "")
    logger.info(f"setup view onboarding {onboarding_id}")
    context = {
        "session_id": session_id,
        "onboarding_id": onboarding_id,
    }
    return render(request, "setup.html", context)



def signup_view(request):
    """Register a new user and restaurant."""

    context = {
        "RECAPTCHA_SITE_KEY": getattr(settings, "RECAPTCHA_SITE_KEY", ""),
        "GOOGLE_API_KEY": getattr(settings, "GOOGLE_API_KEY", ""),
    }
    if request.method != "POST":
        return render(request, "auth/signup.html", context)

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
    place_details_raw = data.get("place_details_json") or ""
    place_details: dict[str, object] | None = None
    if place_details_raw:
        try:
            place_details = json.loads(place_details_raw)
        except (TypeError, json.JSONDecodeError):
            place_details = None

    form_data = {
        "email": email,
        "restaurant_name": restaurant_name,
        "location": location,
    }

    recaptcha_token = data.get("recaptcha_token") or data.get("g-recaptcha-response")
    if recaptcha_token:
        forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR") or ""
        remote_ip = (
            forwarded_for.split(",")[0].strip()
            if forwarded_for
            else request.META.get("REMOTE_ADDR")
        )
        if not _verify_recaptcha(recaptcha_token, remote_ip):
            error_message = "Please complete the security check"
            if is_json:
                return JsonResponse({"error": "recaptcha_failed"}, status=400)
            context.update({"error": error_message, "form_data": form_data})
            return render(request, "auth/signup.html", context)

    if is_json:
        password = (data.get("password") or "").strip()
        if not password:
            return JsonResponse({"error": "password_required"}, status=400)
    else:
        password1 = (data.get("password1") or "").strip()
        password2 = (data.get("password2") or "").strip()
        if password1 != password2:
            context.update({"error": "Passwords do not match", "form_data": form_data})
            return render(request, "auth/signup.html", context)
        password = password1

    if not email or not restaurant_name or not location:
        if is_json:
            return JsonResponse({"error": "missing_fields"}, status=400)
        context.update({"error": "Please complete all fields.", "form_data": form_data})
        return render(request, "auth/signup.html", context)

    if User.objects.filter(username__iexact=email).exists():
        error_message = "An account with that email already exists."
        if is_json:
            return JsonResponse({"error": "email_in_use"}, status=400)
        context.update({"error": error_message, "form_data": form_data})
        return render(request, "auth/signup.html", context)

    try:
        signup_result = signup_service.start_signup(
            email=email,
            password=password,
            restaurant_name=restaurant_name,
            location=location,

        )
        from django.contrib.auth import login
        login(request, signup_result.user)
        if place_details:
            places_by_onboarding = request.session.setdefault(
                "signup_place_details", {}
            )
            places_by_onboarding[str(signup_result.onboarding.uuid)] = place_details
            request.session.modified = True
    except IntegrityError:
        logger.exception("Signup failed due to database error", extra={"email": email})
        error_message = "We couldn't sign you up right now. Please try again."
        if is_json:
            return JsonResponse({"error": "signup_failed"}, status=500)
        context.update({"error": error_message, "form_data": form_data})
        return render(request, "auth/signup.html", context)
    ## get ready to send email.
    activation_token = signup_result.activation_token
    site_url = request.build_absolute_uri("/").rstrip("/")
    activation_link = f"{site_url}/activate/{activation_token}/"
    send_activation_email.delay(email, restaurant_name, activation_link)
    logger.info(f"signup view before return: {signup_result.onboarding.uuid}")
    return create_checkout_session(request, signup_result.onboarding.uuid)



def login_view(request):
    """Authenticate an existing user."""
    if request.user.is_authenticated:
        restaurant_id = (
            models.Restaurant.objects.filter(account__membership__user=request.user)
            .values_list("id", flat=True)
            .first()
        )
        if restaurant_id:
            return redirect("dashboard", restaurant_id=restaurant_id)
        return redirect("getting-started")

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
                return redirect("swipe:home")
            return redirect("getting-started")

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


def activate_email_view(request, token):
    """Activate user email via token and log them in."""
    user_id = signup_service.verify_activation_token(token)

    if not user_id:
        return render(
            request,
            "auth/login.html",
            {
                "error": "This activation link is invalid or has expired. Please try signing up again or contact support.",
            },
        )

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return render(
            request,
            "auth/login.html",
            {"error": "Invalid activation link. Please contact support."},
        )

    if not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])

    login(request, user)

    restaurant_id = (
        models.Restaurant.objects.filter(account__membership__user=user)
        .values_list("id", flat=True)
        .first()
    )
    if restaurant_id:
        return redirect("dashboard", restaurant_id=restaurant_id)
    return redirect("getting-started")


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
    concept_ids = [concept.id for concept in recent_concepts]
    favorite_concept_ids: set[str] = set()
    if concept_ids:
        favorite_concept_ids = set(
            models.FavoriteConcept.objects.filter(
                user=request.user, concept_id__in=concept_ids
            ).values_list("concept_id", flat=True)
        )

    for concept in recent_concepts:
        concept.runtime_display = format_run_duration(concept.ideation_run)
        run_finished = (
            concept.ideation_run.finished_at if concept.ideation_run else None
        )
        concept.generated_at = run_finished or concept.created_at
        concept.is_favorited_for_user = concept.id in favorite_concept_ids
        concept.is_unfavorited_for_user = concept.is_unfavorite

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

    dish_ids = [dish.id for dish in recent_dishes]
    pending_feedback_by_dish: dict[str, int] = {}
    if dish_ids:
        pending_feedback_by_dish = {
            row["feedback__dish_id"]: row["count"]
            for row in models.FeedbackAction.objects.filter(
                feedback__dish_id__in=dish_ids,
                status=models.FeedbackAction.Status.PENDING,
            )
            .values("feedback__dish_id")
            .annotate(count=Count("id"))
        }

    for dish in recent_dishes:
        dish.pending_feedback_count = pending_feedback_by_dish.get(dish.id, 0)
        dish.needs_collab_attention = dish.pending_feedback_count > 0

    menus = list(
        models.MenuCollection.objects.filter(restaurant=restaurant)
        .prefetch_related(
            Prefetch(
                "menuitem_set",
                queryset=models.MenuItem.objects.filter(
                    dish__is_deleted=False
                )
                .select_related(
                    "dish",
                    "dish__parent_concept",
                )
                .order_by("position", "created_at"),
                to_attr="prefetched_menu_items",
            )
        )
        .order_by("-created_at")[:4]
    )
    for menu in menus:
        items = [
            item for item in getattr(menu, "prefetched_menu_items", []) if item.dish
        ]
        menu.menu_items = items

    menu_ids = [menu.id for menu in menus]
    pending_feedback_by_menu: dict[str, int] = {}
    if menu_ids:
        pending_feedback_by_menu = {
            row["feedback__menu_id"]: row["count"]
            for row in models.FeedbackAction.objects.filter(
                feedback__menu_id__in=menu_ids,
                status=models.FeedbackAction.Status.PENDING,
            )
            .values("feedback__menu_id")
            .annotate(count=Count("id"))
        }

    for menu in menus:
        menu.pending_feedback_count = pending_feedback_by_menu.get(menu.id, 0)

    collaboration_actions_qs = models.FeedbackAction.objects.filter(
        feedback__menu__restaurant=restaurant,
        status=models.FeedbackAction.Status.PENDING,
    ).select_related("feedback__menu", "feedback__dish")

    collaboration_updates = [
        {
            "id": action.id,
            "message": _format_feedback_activity(action.feedback),
            "menu_name": getattr(action.feedback.menu, "name", ""),
            "dish_title": getattr(getattr(action.feedback, "dish", None), "title", ""),
            "created_at": action.feedback.created_at,
        }
        for action in collaboration_actions_qs.order_by("-created_at")[:5]
    ]
    pending_collaboration_total = collaboration_actions_qs.count()
    collaboration_updates_more = max(
        pending_collaboration_total - len(collaboration_updates), 0
    )

    user_profile, _ = models.UserProfile.objects.get_or_create(user=request.user)
    
    active_menu_version = getattr(restaurant, "active_menu_version", None)
    has_ready_menu = bool(
        active_menu_version
        and active_menu_version.status == models.MenuVersion.Status.SUCCEEDED
    )

    context = {
        "restaurant": restaurant,
        "trial_info": trial_info,
        "recent_concepts": recent_concepts,
        "recent_dishes": recent_dishes,
        "menus": menus,
        "collaboration_updates": collaboration_updates,
        "pending_collaboration_total": pending_collaboration_total,
        "collaboration_updates_more": collaboration_updates_more,
        "empty_concepts": [],
        "settings_url": reverse("settings"),
        "tbd_message": "Personalized tips will appear here soon.",
        "concept_generate_url": reverse("concepts-generate"),
        "concept_prompt_placeholders": DEFAULT_PROMPT_PLACEHOLDERS,
        #"concept_prompt_suggestions": build_prompt_suggestions(restaurant),
        "classic_creative_slider": slider_value,
        "classic_creative_temperature": slider_temperature_float,
        "creative_bias_label": creative_bias_label,
        "has_seen_welcome": user_profile.has_seen_welcome,
    }
    return render(request, "dashboard.html", context)


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
        concept.is_unfavorited_for_user = concept.is_unfavorite

    recent_disliked = _get_unfavorited_concept_names(restaurant, 6)
    return render(
        request,
        "concepts/grid.html",
        {
            "concepts": concepts,
            "restaurant": restaurant,
            "concept_generate_url": reverse("concepts-generate"),
            "concept_prompt_placeholders": DEFAULT_PROMPT_PLACEHOLDERS,
            #"concept_prompt_suggestions": build_prompt_suggestions(restaurant),
            "classic_creative_slider": slider_value,
            "classic_creative_temperature": slider_temperature,
            "creative_bias_label": creative_bias_label,
            "disliked_concepts": recent_disliked,
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

@csrf_exempt
@login_required
@require_POST
def concepts_generate_view(request):
    membership = (
        models.Membership.objects.filter(user=request.user)
        .select_related("account")
        .first()
    )
    if not membership or not membership.account:
        return HttpResponseForbidden("Restaurant access required.")

    restaurant = models.Restaurant.objects.filter(account=membership.account).first()
    if not restaurant:
        return HttpResponseBadRequest("Restaurant not found for account.")
    raw_prompt = (request.POST.get("prompt") or "").strip()
    user_prompt = raw_prompt[:280]

    slider_value, slider_temperature = _resolve_creativity_settings(restaurant)
    slider_override = _sanitize_slider_value(
        request.POST.get("classic_creative_slider")
    )
    if slider_override is not None:
        _persist_slider_value(restaurant, slider_override)
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
    disliked_context = _get_unfavorited_concept_names(restaurant, 15)

    context = f"""
    Restaurant: {restaurant.name}, {restaurant.location_text}.  \n
    Description: {restaurant.websearch_json}. \n
    Current Restaurant Menu:  {restaurant.menu_json}
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
    if disliked_context:
        context += (
            "\nConcepts the team previously passed on: "
            + ", ".join(disliked_context)
            + ". Consider why they missed the mark and suggest improved twists or alternatives instead of repeating them."
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
                            "ideal_dishes": {"type": "string"},
                            "reasoning": {"type": "string" },
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                                "maxItems": 3
                            }
                        },
                        "required": ["title", "subtitle", "reasoning", "tags","ideal_dishes"],
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
                - **ideal_dishes** Maximum 200 characters
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
                Name: “Harvest Moon Monday”
                Subtitle: “Celebrating autumn's bounty with locally-sourced seasonal ingredients”
                ideal_dishes: “Roasted squash bisque with sage cream, cider-braised pork shoulder, apple-pear galette with honey drizzle”
                Reasoning: “Captured the cozy autumn feeling and farm-to-table movement.”
                Tags: [seasonal, autumn, local-sourcing, comfort-food, farm-to-table, harvest, cozy, regional]

                Name: “Coastal Catch Tuesday”
                Subtitle: “Showcasing the freshest seafood from our local waters”
                ideal_dishes: “Pan-seared halibut with lemon-herb butter, Dungeness crab cakes, sea-salt caramel panna cotta”
                Reasoning: “Leans into coastal identity and freshness; ideal for restaurants near bays or rivers.”
                Tags: [seafood, coastal, local, freshness, sustainability, light-fare, summer, maritime]

                Name: “Woodfire Wednesday”
                Subtitle: “Rustic warmth and smoke-kissed flavor straight from the hearth”
                ideal_dishes: “Wood-grilled flat iron steak with rosemary potatoes, charred vegetable medley, smoked chocolate mousse”
                Reasoning: “Centers on elemental cooking and the sensory experience of fire.”
                Tags: [grill, rustic, smoky, comfort-food, dinner, artisan, bold-flavors, midweek-special]

                Name: “Garden Glow Thursday”
                Subtitle: “A vibrant vegetarian spread celebrating color, texture, and balance”
                ideal_dishes: “Roasted beet and citrus salad, mushroom risotto with truffle oil, lavender panna cotta”
                Reasoning: “Brings visual appeal and wellness focus; ideal for health-conscious diners.”
                Tags: [vegetarian, seasonal, healthy, colorful, light, sustainable, spring, garden-to-table]

                Name: “Fireside Friday”
                Subtitle: “Hearty fare and nostalgic comfort to welcome the weekend”
                ideal_dishes: “Short rib pot pie with puff pastry lid, smoked cheddar mac & cheese, bourbon bread pudding”
                Reasoning: “Invites end-of-week indulgence and evokes cozy camaraderie.”
                Tags: [comfort-food, weekend, hearty, indulgent, nostalgic, winter, fireside, crowd-pleaser]
                ```

                **Goal**: Create concepts that restaurant owners can easily adapt to their local region and seasonal availability while building customer excitement and loyalty.

    """

    if user_prompt:
        system_prompt += (
            "\n                **Special Focus**: Highlight concepts inspired by: "
            + user_prompt
        )

    context_snapshot = {
        "prompt": user_prompt,
        "session_concepts": session_concepts[:15],
        "disliked_concepts": disliked_context,
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
        #logger.info(context)
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
        concept.is_unfavorited_for_user = concept.is_unfavorite

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

@csrf_exempt
@login_required
@require_POST
def concept_favorite_view(request, concept_id):
    concept = get_object_or_404(models.Concept, id=concept_id)
    if concept.restaurant_id:
        has_access = models.Membership.objects.filter(
            user=request.user, account=concept.restaurant.account
        ).exists()
        if not has_access:
            return HttpResponseForbidden("Not allowed to modify this concept.")
    fav, created = models.FavoriteConcept.objects.get_or_create(
        user=request.user, concept=concept, defaults={"favorited_at": timezone.now()}
    )

    is_htmx = request.headers.get("HX-Request") == "true"
    favorited = created

    if not created:
        fav.delete()
        update_fields: List[str] = []
        if concept.sketch_image_url:
            concept.sketch_image_url = None
            update_fields.append("sketch_image_url")
        unfavorite_changed = _record_unfavorited_concept(request.session, concept)
        if unfavorite_changed:
            update_fields.append("is_unfavorite")
        if update_fields:
            concept.save(update_fields=update_fields)
    else:
        concept.is_unfavorited_for_user = False
        if concept.is_unfavorite:
            concept.is_unfavorite = False
            concept.save(update_fields=["is_unfavorite"])

    concept.is_favorited_for_user = favorited
    concept.has_dishes = models.DishIdea.objects.filter(
        parent_concept=concept, is_deleted=False
    ).exists()

    if is_htmx:
        card_html = render_to_string(
            "concepts/_card.html",
            {
                "concept": concept,
                "loading": favorited and not concept.sketch_image_url,
            },
            request=request,
        )
        return HttpResponse(card_html)

    if favorited:
        return redirect("dish_detail", concept_id=concept.id)

    return redirect("concepts")


@csrf_exempt
@login_required
@require_GET
def concept_background_view(request, concept_id):
    """Return the lazy-loaded background sketch for a concept card."""

    concept = get_object_or_404(models.Concept, id=concept_id)
    image_url = concept.sketch_image_url
    if not image_url:
        image_url = llm.generate_concept_sketch(
            concept,
            user=request.user if request.user.is_authenticated else None,
        )
        concept.sketch_image_url = image_url
        concept.save(update_fields=["sketch_image_url"])

    concept.is_favorited_for_user = models.FavoriteConcept.objects.filter(
        user=request.user, concept=concept
    ).exists()
    concept.has_dishes = models.DishIdea.objects.filter(
        parent_concept=concept, is_deleted=False
    ).exists()
    concept.is_unfavorited_for_user = concept.is_unfavorite

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
        concept.is_unfavorited_for_user = concept.is_unfavorite
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

    user_context = user if getattr(user, "is_authenticated", False) else None

    with ThreadPoolExecutor(max_workers=2) as executor:
        enhancement_future = executor.submit(
            llm.enhance_dish,
            dish,
            dish.restaurant,
            user=user_context,
        )
        image_future = executor.submit(
            llm.generate_dish_image_from_prompt,
            prompt=f"Plated dish photo of {dish.title}: {dish.description}",
            default_url=llm.DEFAULT_IMAGE_URL,
            user=user_context,
        )

        try:
            payload = enhancement_future.result()
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning("Enhancement request failed: %s", exc, exc_info=True)
            image_future.cancel()
            return None

        image_url = image_future.result()
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

@csrf_exempt
@login_required
@require_POST
def dishes_generate_view(request, concept_id):
    """Generate nine dish ideas for a concept and return updated content."""
    concept = models.Concept.objects.select_related("restaurant").get(id=concept_id)
    restaurant = concept.restaurant
    has_access = models.Membership.objects.filter(
        user=request.user, account=restaurant.account
    ).exists()
    if not has_access:
        return HttpResponseForbidden("Not allowed to generate dishes for this concept.")
    htmx_request = request.headers.get("HX-Request") == "true"
    slider_value, slider_temperature = _resolve_creativity_settings(restaurant)
    slider_override = _sanitize_slider_value(
        request.POST.get("classic_creative_slider")
    )
    if slider_override is not None:
        _persist_slider_value(restaurant, slider_override)
        slider_value = slider_override
        slider_temperature = (
            Decimal("0.1") + Decimal(slider_value) * Decimal("0.008")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    temperature_float = float(slider_temperature)
    context_text = f"""
        Restaurant: {restaurant.name}, {restaurant.location_text}.  \n
        Description: {restaurant.websearch_markdown}. \n
        Current Restaurant Menu:  {restaurant.menu_json}
        Ingredients: {restaurant.ingredients_json}
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

    run_param = (request.GET.get("run") or "").strip()
    selected_run = None
    if run_param:
        try:
            uuid.UUID(run_param)
        except (TypeError, ValueError):
            run_param = ""
        else:
            selected_run = (
                models.IdeationRun.objects.filter(
                    id=run_param,
                    parent_concept=concept,
                    type=models.IdeationRun.RunType.DISHES,
                    status=models.IdeationRun.Status.SUCCEEDED,
                )
                .order_by("-created_at")
                .first()
            )

    # Get the most recent ideation run for this concept
    latest_run = selected_run or (
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

@csrf_exempt
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


@csrf_exempt
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
    favorite_concepts: list[models.FavoriteConcept] = []
    for favorite in (
        models.FavoriteConcept.objects.filter(user=request.user)
        .select_related("concept", "concept__restaurant")
        .order_by("-favorited_at")
    ):
        concept = favorite.concept
        if not concept:
            continue
        concept.is_favorited_for_user = True
        concept.is_unfavorited_for_user = concept.is_unfavorite
        favorite_concepts.append(favorite)
    favorite_dishes = list(
        models.FavoriteDish.objects.filter(user=request.user)
        .select_related(
            "dish__parent_concept",
            "dish__restaurant",
            "dish__ideation_run",
        )
        .order_by("-favorited_at")
    )

    menus = []
    menu_dishes = []
    menu_color_map: dict[str, str] = {}
    if restaurant:
        menus = list(
            models.MenuCollection.objects.filter(restaurant=restaurant)
            .prefetch_related(
                Prefetch(
                    "menuitem_set",
                    queryset=models.MenuItem.objects.filter(
                        dish__is_deleted=False
                    )
                    .select_related(
                        "dish",
                        "dish__parent_concept",
                        "dish__restaurant",
                        "dish__ideation_run",
                    )
                    .order_by("position", "created_at"),
                    to_attr="prefetched_menu_items",
                )
            )
            .order_by("created_at")
        )
        menu_palette = ["#F9F7FF", "#F3FAFF", "#FFF6F1", "#F4FFF5", "#FFF5FA"]
        for index, menu in enumerate(menus):
            menu_color_map[menu.name] = menu_palette[index % len(menu_palette)]
            items = [
                item
                for item in getattr(menu, "prefetched_menu_items", [])
                if item.dish
            ]
            menu.menu_items = items
            for item in items:
                menu_dishes.append(item.dish)

    concept_menu_map: dict[uuid.UUID, list[str]] = {}
    dish_menu_map: dict[uuid.UUID, list[str]] = {}
    for menu in menus:
        menu_name = menu.name
        for item in getattr(menu, "menu_items", []) or []:
            dish = getattr(item, "dish", None)
            if not dish:
                continue
            dish_entry = dish_menu_map.setdefault(dish.id, [])
            if menu_name not in dish_entry:
                dish_entry.append(menu_name)
            concept = getattr(dish, "parent_concept", None)
            if not concept:
                continue
            concept_entry = concept_menu_map.setdefault(concept.id, [])
            if menu_name not in concept_entry:
                concept_entry.append(menu_name)

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
        "concept_menu_map": concept_menu_map,
        "dish_menu_map": dish_menu_map,
        "menu_color_map": menu_color_map,
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
        update_fields: List[str] = []
        if concept.sketch_image_url:
            concept.sketch_image_url = None
            update_fields.append("sketch_image_url")
        if _record_unfavorited_concept(request.session, concept):
            update_fields.append("is_unfavorite")
        if update_fields:
            concept.save(update_fields=update_fields)
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
        .select_related("restaurantsettings")
        .first()
    )

    prefs = getattr(request.user, "notificationpref", None)
    #active_menu = restaurant.active_menu_version if restaurant else None
    disliked_concepts = _get_session_list(request.session, "disliked_concepts")
    recent_disliked = list(reversed(disliked_concepts[-6:])) if disliked_concepts else []
    return render(request, "settings/main.html", {
        "restaurant": restaurant,
        #"ingredients": ingredients,
        "prefs": prefs,
        "restaurant_settings": getattr(restaurant, "restaurantsettings", None),
        #"active_menu_version": active_menu,
        "disliked_concepts": recent_disliked,
    })


@require_POST
def update_creativity(request, restaurant_id):
    restaurant = get_object_or_404(models.Restaurant, id=restaurant_id)
    slider_value = _sanitize_slider_value(request.POST.get("classic_creative_slider"))
    if slider_value is not None:
        _persist_slider_value(restaurant, slider_value)
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def update_notifications(request):
    prefs, _ = models.NotificationPref.objects.get_or_create(user=request.user)
    prefs.on_background_complete_email = "on_background_complete_email" in request.POST
    prefs.on_new_menu_version_email = "on_new_menu_version_email" in request.POST
    prefs.save()
    return redirect("settings")


@login_required
@require_POST
def dismiss_welcome_view(request):
    """Mark the welcome message as seen for the current user."""
    
    profile, _ = models.UserProfile.objects.get_or_create(user=request.user)
    profile.has_seen_welcome = True
    profile.save(update_fields=["has_seen_welcome"])
    
    return JsonResponse({"success": True})


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
    metadata = {"account_id": str(account.id)}
    next_path = request.POST.get("next") or reverse("billing")
    if not isinstance(next_path, str) or not next_path.startswith("/"):
        next_path = reverse("billing")
    base_success_url = request.build_absolute_uri(next_path)
    success_joiner = "&" if "?" in base_success_url else "?"
    success_url = f"{base_success_url}{success_joiner}session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = base_success_url

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

    _ensure_stripe_api_key()

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
    active_version = getattr(restaurant, "active_menu_version", None)
    menu_text = ""
    if active_version and active_version.raw_markdown:
        menu_text = active_version.raw_markdown
    context = {"restaurant": restaurant, "errors": [], "menu_text": menu_text}
    return render(request, "_partials/menu_modal.html", context)


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
        try:
            submitted_urls = request.POST.getlist("menu_url")
            menu_text = (request.POST.get("menu_text") or "").strip()
        except RequestDataTooBig:
            context = {
                "restaurant": restaurant,
                "errors": [
                    "Your menu content is too large to paste directly. Please upload a PDF instead.",
                ],
                "menu_text": "",
            }
            response = render(
                request,
                "_partials/menu_modal.html",
                context,
                status=413,
            )
            response["HX-Retarget"] = "#menu-modal"
            response["HX-Reswap"] = "innerHTML"
            return response

        menu_pdf = request.FILES.get("menu_pdf")
        menu_urls = [url.strip() for url in submitted_urls if url and url.strip()]
        if submitted_urls:
            restaurant.set_menu_urls(menu_urls)
            restaurant.save(update_fields=["menu_urls", "primary_menu_url"])

        menu_url = None
        if not menu_text and not menu_pdf and menu_urls:
            menu_url = menu_urls[0]

        errors: list[str] = []
        if not (menu_url or menu_text or menu_pdf):
            errors.append(
                "Add at least one menu URL, paste content, or upload a PDF."
            )
        else:
            menu_version = _process_menu_submission(
                restaurant, menu_url, menu_text, menu_pdf
            )
            if not menu_version:
                errors.append(
                    "We couldn't process your submission. Please try again."
                )

        if errors:
            context = {
                "restaurant": restaurant,
                "errors": errors,
                "menu_text": menu_text,
            }
            response = render(
                request,
                "_partials/menu_modal.html",
                context,
                status=400,
            )
            response["HX-Retarget"] = "#menu-modal"
            response["HX-Reswap"] = "innerHTML"
            return response

    return restaurant_status(request, restaurant_id)

@csrf_exempt
@require_POST
def github_webhook(request):
    # Verify GitHub secret
    secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    if secret:
        import subprocess

        # inside deploy_webhook
        subprocess.run(["/home/django/deploy.sh"])
        return HttpResponse("OK\n")
    return HttpResponse("Ignored\n")
