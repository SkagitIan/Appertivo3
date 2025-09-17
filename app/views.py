"""Application views."""

import json, os
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from . import models
from django.template.loader import render_to_string
from pydantic import BaseModel
from typing import List

from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()
_openai_api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=_openai_api_key) if _openai_api_key else None

class ConceptList(BaseModel):
    concepts: List[str]

from app import llm, models
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
                                "title": {"type": "string", "maxLength": 30},
                                "subtitle": {"type": "string", "maxLength": 50}
                            },
                            "required": ["title", "subtitle"],
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
                    "Generate exactly 9 unique, theme-based concepts for daily specials.  Include a name no more than 30 characters & a subtitle no more than 80 characters. "
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
                rank_order=idx,
            )
            for idx, item in enumerate(names, start=1)
        ]


    return render(request, "concepts/_concepts_grid.html", {"concepts": concepts})

@login_required
def concept_favorite_view(request, concept_id):
    concept = get_object_or_404(models.Concept, id=concept_id)
    fav, created = models.FavoriteConcept.objects.get_or_create(
        user=request.user, concept=concept, defaults={"favorited_at": timezone.now()}
    )
    favorited = created
    if not created:
        fav.delete()
        favorited = False

    html = render_to_string(
        "concepts/_favorite_button.html",
        {"concept": concept, "favorited": favorited},
        request=request,
    )
    return HttpResponse(html)

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
    fav, created = models.FavoriteDish.objects.get_or_create(
        user=request.user, dish=dish, defaults={"favorited_at": timezone.now()}
    )
    if not created:
        fav.delete()
        favorited = False
    else:
        favorited = True

    dish.is_favorited = favorited  # attach attribute for rendering

    html = render_to_string("dishes/_favorite_button.html", {"dish": dish}, request=request)
    return HttpResponse(html)


def dish_variation_view(request, dish_id):
    """Return a variation of a dish."""
    dish = get_object_or_404(models.DishIdea, id=dish_id)
    variation = llm.generate_dishes(dish.title)[0]
    return JsonResponse({"title": variation["title"]})


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
        .select_related("concept__restaurant")
        .order_by("-favorited_at")
    )
    favorite_dishes = (
        models.FavoriteDish.objects.filter(user=request.user)
        .select_related("dish__parent_concept", "dish__restaurant")
        .order_by("-favorited_at")
    )
    ctx = {
        "restaurant": restaurant,
        "favorite_concepts": favorite_concepts,
        "favorite_dishes": favorite_dishes,
    }
    return render(request, "favorites/dashboard.html", ctx)


def favorite_remove_view(request, type, id):
    """Remove a favorite concept or dish."""
    if type == "concept":
        models.FavoriteConcept.objects.filter(user=request.user, concept_id=id).delete()
    else:
        models.FavoriteDish.objects.filter(user=request.user, dish_id=id).delete()
    return JsonResponse({"removed": True})


def menu_collection_create_view(request):
    """Create a new menu collection."""
    name = request.POST.get("name", "Menu")
    restaurant = models.Restaurant.objects.first()
    menu = models.MenuCollection.objects.create(
        restaurant=restaurant, created_by_user=request.user, name=name
    )
    return JsonResponse({"id": str(menu.id), "name": menu.name})


def menu_item_add_view(request, dish_id, collection_id):
    """Add a dish to a menu collection."""
    dish = get_object_or_404(models.DishIdea, id=dish_id)
    menu = get_object_or_404(models.MenuCollection, id=collection_id)
    models.MenuItem.objects.create(menu=menu, dish=dish, position=1)
    return JsonResponse({"added": True})


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

    menu_url = (request.POST.get("menu_url") or "").strip() or None
    restaurant.primary_menu_url = menu_url
    restaurant.save(update_fields=["primary_menu_url"])

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


import logging

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


from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

def upload_menu(request, restaurant_id):
    restaurant = get_object_or_404(models.Restaurant, id=restaurant_id)

    if request.method == "POST":
        menu_url = (request.POST.get("menu_url") or "").strip()
        menu_text = (request.POST.get("menu_text") or "").strip()
        menu_pdf = request.FILES.get("menu_pdf")

        if menu_url:
            restaurant.primary_menu_url = menu_url
            restaurant.save(update_fields=["primary_menu_url"])
            mv = models.MenuVersion.objects.create(
                restaurant=restaurant,
                source_url=menu_url,
                source_kind=models.MenuVersion.SourceKind.URL_SCRAPE,
                raw_markdown="",
                status=models.MenuVersion.Status.QUEUED,
            )
            transaction.on_commit(lambda mv_id=str(mv.id): scrape_menu.delay(mv_id))

        elif menu_text:
            mv = models.MenuVersion.objects.create(
                restaurant=restaurant,
                source_kind=models.MenuVersion.SourceKind.PASTED_TEXT,
                raw_markdown=menu_text,
                status=models.MenuVersion.Status.SUCCEEDED,
            )
            restaurant.active_menu_version = mv
            restaurant.save(update_fields=["active_menu_version"])

        elif menu_pdf:
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
            transaction.on_commit(
                lambda mv_id=str(mv.id), storage_path=path: parse_pdf_menu.delay(
                    mv_id, storage_path
                )
            )

    return restaurant_status(request, restaurant_id)
