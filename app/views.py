"""Application views."""

import json, logging, os, uuid
from typing import Iterable, List, Optional

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import Max, Prefetch
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from pydantic import BaseModel

from . import models

from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()
_openai_api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=_openai_api_key) if _openai_api_key else None

class ConceptList(BaseModel):
    concepts: List[str]

from app import llm
from .tasks import parse_pdf_menu, run_outscraper_search, scrape_menu

def dish_grid(request, concept_name: str):
    """Render a 3x3 grid of dishes for a concept."""
    dishes = llm.generate_dishes(concept_name)
    ctx = {"concept": concept_name, "dishes": dishes}
    return render(request, "app/dish_grid.html", ctx)


def home_view(request):
    """Landing page with signup/login links."""
    return render(request, "home.html")


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
        raw_menu_url = (data.get("menu_url") or "").strip()
        menu_url = raw_menu_url or None
        form_data = {
            "email": email,
            "restaurant_name": restaurant_name,
            "location": location,
            "menu_url": raw_menu_url,
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
                    primary_menu_url=menu_url,
                    menu_urls=[menu_url] if menu_url else [],
                )

                if menu_url:
                    mv = models.MenuVersion.objects.create(
                        restaurant=restaurant,
                        source_url=menu_url,
                        source_kind=models.MenuVersion.SourceKind.URL_SCRAPE,
                        raw_markdown="",
                        status=models.MenuVersion.Status.QUEUED,
                    )
                    transaction.on_commit(
                        lambda mv_id=str(mv.id): scrape_menu.delay(mv_id)
                    )
                else:
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
        redirect_url = reverse("dashboard", args=[restaurant.id])
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
        models.Restaurant.objects.select_related("account"),
        id=restaurant_id
    )
    recent_runs = (
        models.IdeationRun.objects.filter(restaurant=restaurant)
        .order_by("-created_at")[:5]
    )
    subscription = (
        models.Subscription.objects.filter(account=restaurant.account)
        .order_by("-created_at")
        .first()
    )
    context = {
        "restaurant": restaurant,
        "recent_runs": recent_runs,
        "subscription_status": getattr(subscription, "status", "free"),
        "prompt_for_menu": not bool(restaurant.primary_menu_url),
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
    return render(request, "menus/main.html")


def onboarding_view(request):
    """Show onboarding progress."""
    jobs = models.Job.objects.filter(user=request.user)
    return render(request, "onboarding.html", {"jobs": jobs})


def onboarding_status_view(request):
    """Return simple onboarding status."""
    return JsonResponse({"status": "pending"})


def manual_menu_view(request):
    """Allow manual menu entry."""
    if request.method == "POST":
        return JsonResponse({"status": "queued"})
    return render(request, "_partials/manual_menu.html")


@login_required
def concepts_view(request):
    """Display latest concepts with favorite state for the user."""
    concepts_qs = models.Concept.objects.order_by("-created_at")
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
    return render(request, "concepts/grid.html", {"concepts": concepts})


@login_required
def concepts_generate_view(request):
    """Generate 9 new concepts via OpenAI."""
    restaurant = models.Restaurant.objects.first()

    # Build context (menu, outscraper, etc.)
    context = f"Restaurant: {restaurant.name}, {restaurant.location_text}"
    if restaurant.active_menu_version:
        context += f"\nMenu:\n{restaurant.active_menu_version.raw_markdown[:2000]}"

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
                                "items": {"type": "string", "maxLength": 20},
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



    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "system",
                "content": (
                    "You are a seasoned restaurant marketing consultant. "
                    "Generate exactly 9 unique, theme-based concepts for daily specials."
                    "Include a name no more than 30 characters & a subtitle no more than 80 characters. "
                    "Include a reasoning of why you chose this concept, what context and frame of mind were you in when you picked this concept.  1 sentance ender 80 characters"
                    "Include an array of tags that quickly relate the concept to the users context"
                    "Concepts are themes like 'Taco Tuesday', 'Family Feast', 'Game Night', "
                    "'Seasonal Harvest Dinner'. They are NOT individual dishes."
                ),
            },
            {"role": "user", "content": context},
        ],
        text={"format": schema},
    )

    # 👇 Extract raw text and parse
    raw_text = response.output[0].content[0].text
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        data = {"concepts": []}  # fallback if model didn’t follow schema

    names = data.get("concepts", [])


    # Save ideation run
    run = models.IdeationRun.objects.create(
        restaurant=restaurant,
        initiated_by_user=request.user,
        type=models.IdeationRun.RunType.CONCEPTS,
        model_name="gpt-4.1-mini",
        temperature=0.5,
        classic_creative=50,
        context_snapshot={"context": context},
        status=models.IdeationRun.Status.SUCCEEDED,
    )

    concepts = [
        models.Concept.objects.create(
            restaurant=restaurant,
            ideation_run=run,
            name=item["title"],
            subtitle=item["subtitle"],
            reasoning=item["reasoning"],
            tags=item["tags"],
            rank_order=idx,
        )
        for idx, item in enumerate(names, start=1)
    ]


    for concept in concepts:
        concept.is_favorited_for_user = False

    return render(request, "concepts/_concepts_grid.html", {"concepts": concepts})

@login_required
def concept_favorite_view(request, concept_id):
    concept = get_object_or_404(models.Concept, id=concept_id)
    fav, created = models.FavoriteConcept.objects.get_or_create(
        user=request.user, concept=concept, defaults={"favorited_at": timezone.now()}
    )
    image_url = concept.sketch_image_url
    favorited = created

    if created:
        image_url = None
    else:
        fav.delete()
        favorited = False
        if image_url:
            concept.sketch_image_url = None
            concept.save(update_fields=["sketch_image_url"])
        image_url = None

    concept.is_favorited_for_user = favorited

    # Always return the updated button immediately
    button_html = render_to_string(
        "concepts/_favorite_button.html",
        {"concept": concept, "favorited": favorited, "trigger_loader": favorited},
        request=request,
    )
    if favorited:
        return HttpResponse(button_html)

    background_html = render_to_string(
        "concepts/_concept_background.html",
        {"concept": concept, "image_url": None, "swap_oob": True},
        request=request,
    )
    return HttpResponse(button_html + background_html)



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
    return render(
        request,
        "concepts/_concept_background.html",
        {"concept": concept, "image_url": image_url},
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
        if not concept.sketch_image_url:
            image_url = llm.generate_concept_sketch(concept)
            concept.sketch_image_url = image_url
            concept.save(update_fields=["sketch_image_url"])
        concept.is_favorited_for_user = True
        concepts.append(concept)

    return render(
        request,
        "concepts/_favorites_section.html",
        {"concepts": concepts},
    )

def serialize_restaurant_context(restaurant_payload, menu_markdown, concept):
    """Return a slim JSON-serializable context for dish generation."""
    return {
        "restaurant": {
            "name": restaurant_payload.get("name"),
            "description": restaurant_payload.get("description"),
            "category": restaurant_payload.get("category"),
            "price_range": restaurant_payload.get("range"),
            "city": restaurant_payload.get("city"),
            "state": restaurant_payload.get("us_state"),
            "atmosphere": restaurant_payload.get("about", {}).get("Atmosphere", {}),
            "highlights": restaurant_payload.get("about", {}).get("Highlights", {}),
            "popular_for": restaurant_payload.get("about", {}).get("Popular for", {}),
            "offerings": restaurant_payload.get("about", {}).get("Offerings", {}),
            "customer_favorites": restaurant_payload.get("reviews_tags", []),
            "rating": restaurant_payload.get("rating"),
        },
        "menu_markdown": menu_markdown,
        "concept": {
            "id": str(concept.id),
            "name": concept.name,
        },
    }


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


def ensure_dish_enhancement(
    dish: models.DishIdea, user: Optional[User]
) -> Optional[models.Enhancement]:
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

    image_url = payload.get("image_url") or llm.DEFAULT_IMAGE_URL
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
    concept = models.Concept.objects.get(id=concept_id)
    
    # Example: pull restaurant + menu from your DB relations
    restaurant = concept.restaurant
    restaurant_payload = restaurant.context_json  # assuming JSONField
    menu_markdown = restaurant.primary_menu_url or ""

    context = serialize_restaurant_context(restaurant_payload, menu_markdown, concept)

    logger.info("Generating dishes for concept=%s restaurant=%s", concept.name, restaurant.name)

    # ✅ Use Structured Outputs with enforced schema
    schema = {
            "name": "dish_list",
            "schema": {
                "type": "object",
                "properties": {
                "dishes": {
                    "type": "array",
                    "items": {
                    "type": "object",
                    "properties": {
                        "title": { "type": "string" },
                        "description": { "type": "string" },
                        "ingredient_overlap": {
                        "type": "array",
                        "items": { "type": "string" }
                        },
                        "category_tags": {
                        "type": "array",
                        "items": { "type": "string" }
                        }
                    },
                    "required": ["title", "description", "ingredient_overlap", "category_tags"],
                    "additionalProperties": False
                    },
                    "minItems": 9,
                    "maxItems": 9
                }
                },
                "required": ["dishes"],
                "additionalProperties": False
            },
            "type": "json_schema",
            "strict": True
            }

    try:
        response = client.responses.create(
            model="gpt-4.1",  # or gpt-4o-mini if you want faster
            input=[
                {
                    "role": "user",
                    "content": f"""
                    Given the following restaurant context and menu, generate 9 saleable dish ideas
                    for the concept: '{concept.name}'.
                    Each dish must include: title, description, ingredient_overlap, category_tags.
                    """,
                },
                {
                    "role": "user",
                    "content": json.dumps(context, indent=2),
                },
            ],
            text={"format":schema},
        )

        # Parse structured output
        raw_text = response.output[0].content[0].text
        parsed = json.loads(raw_text)
        dishes = parsed["dishes"]

        logger.info("Generated %d dishes for concept=%s", len(dishes), concept.name)
        ideation_run = models.IdeationRun.objects.create(
            restaurant=restaurant,
            initiated_by_user=request.user,
            type=models.IdeationRun.RunType.DISHES,
            model_name="gpt-4.1",
            temperature=0.7,
            classic_creative=50,
            context_snapshot=context,
            parent_concept=concept,
            status=models.IdeationRun.Status.RUNNING,
                )

        # Save to DB with UUIDs
        dish_objects = []
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

        # Mark run as complete
        ideation_run.status = models.IdeationRun.Status.SUCCEEDED
        ideation_run.save(update_fields=["status"])
        dishes = dish_objects

    except Exception as e:
        logger.error("Dish generation failed: %s", str(e), exc_info=True)
        ideation_run.status = models.IdeationRun.Status.FAILED
        ideation_run.error_message = str(e)
        ideation_run.save(update_fields=["status", "error_message"])
    return render(request, "dishes/grid.html", {"concept": concept, "dishes": dishes})


def dishes_grid_view(request, concept_id):
    concept = get_object_or_404(models.Concept, id=concept_id)
    restaurant = concept.restaurant
    ideation_run = concept.ideation_run

    # === OpenAI call (already in your code) ===
    raw_text = response.output[0].content[0].text
    dishes_obj = json.loads(raw_text)

    dishes = []
    for d in dishes_obj["dishes"]:
        dish_obj, created = models.DishIdea.objects.get_or_create(
            restaurant=restaurant,
            ideation_run=ideation_run,
            parent_concept=concept,
            title=d["title"],
            defaults={
                "description": d["description"],
                "ingredient_names": d.get("ingredient_overlap", []),
                "category_tags": d.get("category_tags", []),
            },
        )
        dishes.append(dish_obj)

    dishes = decorate_dishes_with_enhancements(dishes)

    # === mark favorites ===
    user_favs = set(
        models.FavoriteDish.objects.filter(user=request.user)
        .values_list("dish_id", flat=True)
    )
    for dish in dishes:
        dish.is_favorited = dish.id in user_favs

    return render(
        request,
        "dishes/grid.html",
        {"concept": concept, "dishes": dishes}
    )


def dish_favorite_view(request, dish_id):
    """Toggle favorite on a dish."""
    dish = get_object_or_404(models.DishIdea, id=dish_id)
    card_context = request.POST.get("context") or request.GET.get("context") or "grid"
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
        {"dish": dish, "card_context": card_context},
        request=request,
    )
    return HttpResponse(html)


@login_required
@require_POST
def dish_delete_view(request, dish_id):
    """Delete a dish and remove any associated enhancement assets."""

    dish = get_object_or_404(
        models.DishIdea.objects.select_related("restaurant"),
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

    dish.delete()

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
        ),
        id=dish_id,
    )

    base_dish = dish.parent_dish or dish
    concept = dish.parent_concept
    restaurant = dish.restaurant

    restaurant_payload = restaurant.context_json or {}
    menu_markdown = restaurant.primary_menu_url or ""
    context = serialize_restaurant_context(restaurant_payload, menu_markdown, concept)

    existing_variations = list(
        models.DishIdea.objects.filter(parent_dish=base_dish).order_by("created_at")
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
        "context": context,
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

    html = render_to_string(
        "dishes/_card.html", {"dish": new_dish, "card_context": "grid"}, request=request
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
    favorite_concepts = (
        models.FavoriteConcept.objects.filter(user=request.user)
        .select_related("concept", "concept__restaurant")
        .order_by("-favorited_at")
    )
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
    if all_dishes:
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
        "menus_payload_json": json.dumps(menus_payload),
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
    name = request.POST.get("name", "Menu")
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
    dish = get_object_or_404(models.DishIdea, id=dish_id)
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

    dish = get_object_or_404(models.DishIdea, id=dish_id)

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


logger = logging.getLogger(__name__)

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



def billing_view(request):
    """Show billing page."""
    return render(request, "billing/main.html")


def billing_upgrade_view(request):
    """Start upgrade flow."""
    return JsonResponse({"status": "ok"})


def billing_cancel_view(request):
    """Cancel subscription."""
    return JsonResponse({"status": "ok"})


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
    restaurant = get_object_or_404(models.Restaurant, id=restaurant_id)
    payload = (
        models.OutscraperPayload.objects.filter(restaurant=restaurant)
        .order_by("-created_at")
        .first()
    )
    context = {
        "restaurant": restaurant,
        "menu_version": restaurant.active_menu_version,
        "payload": payload,
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
        menu_url = (request.POST.get("menu_url") or "").strip()
        menu_text = (request.POST.get("menu_text") or "").strip()
        menu_pdf = request.FILES.get("menu_pdf")

        _process_menu_submission(restaurant, menu_url, menu_text, menu_pdf)

    return restaurant_status(request, restaurant_id)
