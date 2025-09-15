"""Application views."""

import json

from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .tasks import run_outscraper_search, scrape_menu, parse_pdf_menu
from django.urls import reverse
from app import llm, models

def concept_grid(request):
    """Render a 3x3 grid of concept names."""
    concepts = llm.generate_concepts()
    return render(request, "app/concept_grid.html", {"concepts": concepts})


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
        menu_url = (data.get("menu_url") or "").strip() or None

        if is_json:
            password = data.get("password")
            if not password:
                return JsonResponse({"error": "password_required"}, status=400)
        else:
            password1 = data.get("password1")
            password2 = data.get("password2")
            if password1 != password2:
                return render(
                    request, "auth/signup.html", {"error": "Passwords do not match"}
                )
            password = password1

        if not email or not restaurant_name or not location:
            if is_json:
                return JsonResponse({"error": "missing_fields"}, status=400)
            return render(
                request,
                "auth/signup.html",
                {"error": "Please complete all fields."},
            )

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
        username = request.POST.get("username")
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
            return redirect("/dashboard/")
        return render(request, "auth/login.html", {"error": "invalid"})
    return render(request, "auth/login.html")


def dashboard(request, restaurant_id):
    restaurant = get_object_or_404(models.Restaurant, id=restaurant_id)
    return render(request, "dashboard.html", {"restaurant": restaurant})


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


def concepts_view(request):
    """Display latest concepts."""
    concepts = models.Concept.objects.order_by("-created_at")[:9]
    return render(request, "concepts/grid.html", {"concepts": concepts})


def concepts_generate_view(request):
    """Generate new concepts via mock LLM."""
    names = llm.generate_concepts()
    restaurant = models.Restaurant.objects.first()
    run = models.IdeationRun.objects.create(
        restaurant=restaurant,
        initiated_by_user=request.user if request.user.is_authenticated else None,
        type=models.IdeationRun.RunType.CONCEPTS,
        model_name="mock",
        temperature=0.5,
        classic_creative=50,
        context_snapshot={},
        status=models.IdeationRun.Status.SUCCEEDED,
    )
    concepts = []
    for idx, name in enumerate(names, start=1):
        concepts.append(
            models.Concept.objects.create(
                restaurant=restaurant,
                ideation_run=run,
                name=name,
                rank_order=idx,
            )
        )
    return render(request, "concepts/grid.html", {"concepts": concepts})


def concept_favorite_view(request, concept_id):
    """Toggle favorite on a concept."""
    concept = get_object_or_404(models.Concept, id=concept_id)
    fav, created = models.FavoriteConcept.objects.get_or_create(
        user=request.user, concept=concept, defaults={"favorited_at": timezone.now()}
    )
    if not created:
        fav.delete()
        favorited = False
    else:
        favorited = True
    return JsonResponse({"favorited": favorited})


def dishes_view(request, concept_id):
    """Show dishes for a concept."""
    dishes = models.DishIdea.objects.filter(parent_concept_id=concept_id)[:9]
    return render(request, "dishes/grid.html", {"dishes": dishes})


def dish_generate_view(request, concept_id):
    """Generate dishes for a concept."""
    concept = get_object_or_404(models.Concept, id=concept_id)
    ideas = llm.generate_dishes(concept.name)
    restaurant = models.Restaurant.objects.first()
    run = models.IdeationRun.objects.create(
        restaurant=restaurant,
        initiated_by_user=request.user if request.user.is_authenticated else None,
        type=models.IdeationRun.RunType.DISHES,
        model_name="mock",
        temperature=0.5,
        classic_creative=50,
        context_snapshot={},
        parent_concept=concept,
        status=models.IdeationRun.Status.SUCCEEDED,
    )
    dishes = []
    for idea in ideas:
        dishes.append(
            models.DishIdea.objects.create(
                restaurant=restaurant,
                ideation_run=run,
                parent_concept=concept,
                title=idea["title"],
                description=idea["description"],
                ingredient_names=idea["ingredient_overlap"],
                category_tags=idea["category_tags"],
            )
        )
    return render(request, "dishes/grid.html", {"dishes": dishes})


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
    return JsonResponse({"favorited": favorited})


def dish_variation_view(request, dish_id):
    """Return a variation of a dish."""
    dish = get_object_or_404(models.DishIdea, id=dish_id)
    variation = llm.generate_dishes(dish.title)[0]
    return JsonResponse({"title": variation["title"]})


def favorites_view(request):
    """Render favorites dashboard."""
    concepts = models.FavoriteConcept.objects.filter(user=request.user)
    dishes = models.FavoriteDish.objects.filter(user=request.user)
    ctx = {"concepts": concepts, "dishes": dishes}
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
    restaurant = models.Restaurant.objects.filter(account__membership__user=request.user).first()
    ingredients = list(
        models.Ingredient.objects.filter(restaurant=restaurant).values_list("name", flat=True)
    )
    prefs = getattr(request.user, "notificationpref", None)
    return render(request, "settings/main.html", {
        "restaurant": restaurant,
        "ingredients": ingredients,
        "prefs": prefs,
    })


@login_required
@require_POST
def update_restaurant_info(request):
    restaurant = models.Restaurant.objects.filter(account__membership__user=request.user).first()
    restaurant.primary_menu_url = request.POST.get("menu_url")
    restaurant.save(update_fields=["primary_menu_url"])

    ingredient_names = request.POST.get("ingredients", "").split(",")
    for name in ingredient_names:
        models.Ingredient.objects.get_or_create(restaurant=restaurant, name=name.strip())

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
