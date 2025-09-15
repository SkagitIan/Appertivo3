"""Application views."""

import json

from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login
from django.utils import timezone
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from app import llm, models


@csrf_exempt
@require_POST
def signup(request):
    """Handle user signup and initial restaurant creation."""
    data = json.loads(request.body.decode("utf-8"))
    email = data["email"]
    password = data["password"]
    restaurant_name = data["restaurant_name"]
    location = data["location"]
    menu_url = data.get("menu_url")

    with transaction.atomic():
        user = User.objects.create_user(username=email, email=email, password=password)
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
            models.OutscraperPayload.objects.create(
                restaurant=restaurant,
                status=models.OutscraperPayload.Status.QUEUED,
                request_params={"menu_url": menu_url},
                discovered_menu_url=menu_url,
            )

    return JsonResponse({"status": "queued"})


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


@csrf_exempt
def signup_view(request):
    """Register a new user and restaurant."""
    if request.method == "POST":
        email = request.POST["email"]
        password = request.POST["password"]
        restaurant_name = request.POST["restaurant_name"]
        location = request.POST["location"]
        with transaction.atomic():
            user = User.objects.create_user(
                username=email, email=email, password=password
            )
            models.UserProfile.objects.create(user=user)
            account = models.Account.objects.create(name=restaurant_name)
            models.Membership.objects.create(
                account=account, user=user, role=models.Membership.Role.OWNER
            )
            models.Restaurant.objects.create(
                account=account, name=restaurant_name, location_text=location
            )
        return redirect("/onboarding/")
    return render(request, "auth/signup.html")


def login_view(request):
    """Authenticate an existing user."""
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect("/concepts/")
        return render(request, "auth/login.html", {"error": "invalid"})
    return render(request, "auth/login.html")


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


def settings_view(request):
    """Display settings page."""
    restaurant = models.Restaurant.objects.first()
    settings = models.RestaurantSettings.objects.filter(restaurant=restaurant).first()
    ingredients = models.Ingredient.objects.filter(restaurant=restaurant)
    ctx = {"restaurant": restaurant, "settings": settings, "ingredients": ingredients}
    return render(request, "settings/main.html", ctx)


def settings_rescrape_menu_view(request):
    """Trigger a menu rescrape."""
    return JsonResponse({"status": "queued"})


def settings_slider_update_view(request):
    """Update creative slider."""
    restaurant = models.Restaurant.objects.first()
    settings = models.RestaurantSettings.objects.get(restaurant=restaurant)
    value = int(request.POST.get("value", settings.classic_creative_slider))
    settings.classic_creative_slider = value
    settings.save()
    return JsonResponse({"value": settings.classic_creative_slider})


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
