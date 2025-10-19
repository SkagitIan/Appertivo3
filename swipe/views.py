import asyncio
import logging
from uuid import UUID

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse, QueryDict
from django.db.models import Prefetch
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView, View
from django.urls import reverse
from app.llm import *
from app.models import Restaurant, RestaurantSettings
# Stub imports for future service integration
# from .services import generate_concepts_batch  # to be implemented
from types import SimpleNamespace


def _resolve_restaurant_settings(restaurant):
    if not restaurant:
        return SimpleNamespace(classic_creative_slider=50)
    settings, _ = RestaurantSettings.objects.get_or_create(restaurant=restaurant)
    return settings

from swipe.llm_utils import GetConcepts
from swipe.demo_data import build_demo_state
from swipe.models import Concept, Dish
from django.shortcuts import get_object_or_404
logger = logging.getLogger(__name__)

@csrf_exempt
def generate_concepts_view(request, restaurant_id):
    """
    Fetch a restaurant and generate 3 concepts (each with 3 dishes).
    Returns JSON output for verification.
    """
    try:
        restaurant = get_object_or_404(Restaurant, id=restaurant_id)
        restaurant_context = restaurant.context
        generator = GetConcepts(restaurant=restaurant, restaurant_context=restaurant_context)

        logger.info(f"Starting concept generation for restaurant: {restaurant.name}")
        results = asyncio.run(generator.generate_batch())

        return JsonResponse(
            {"status": "success", "restaurant": restaurant.name, "results": results},
            safe=False,
            json_dumps_params={"indent": 2},
        )

    except Exception as e:
        logger.exception("Concept generation failed.")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)

# --- HTML ---
class SwipeHomeView(TemplateView):
    template_name = "swipe/index.html"

    def get_restaurant(self):
        restaurant_id = self.kwargs.get("restaurant_id") or self.request.GET.get("restaurant_id")
        if restaurant_id:
            try:
                return get_object_or_404(Restaurant, id=restaurant_id)
            except (TypeError, ValueError):
                logger.warning("Invalid restaurant_id supplied: %s", restaurant_id)
        return Restaurant.objects.order_by("-created_at").first()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        restaurant = self.get_restaurant()
        concepts = []

        if restaurant:
            concept_qs = (
                Concept.objects.filter(
                    restaurant=restaurant,
                    is_deleted=False,
                )
                .order_by("-created_at")
                .prefetch_related(
                    Prefetch(
                        "dishes",
                        queryset=Dish.objects.filter(is_deleted=False).order_by("id"),
                    )
                )
            )
            concepts = list(concept_qs)

        dish_counts = [len(list(c.dishes.all())) for c in concepts]
        restaurant_settings = _resolve_restaurant_settings(restaurant)
        update_creativity_url = (
            reverse("update_creativity", args=[restaurant.id]) if restaurant else "#"
        )

        context.update(
            {
                "restaurant": restaurant,
                "concepts": concepts,
                "dish_counts": dish_counts,
                "restaurant_settings": restaurant_settings,
                "update_creativity_url": update_creativity_url,
            }
        )
        return context


class SwipeDemoView(SwipeHomeView):
    template_name = "swipe/index.html"
    DEMO_RESTAURANT_ID = UUID("83647628-3514-4224-b1dc-701519004db8")

    def get_restaurant(self):
        return get_object_or_404(Restaurant, id=self.DEMO_RESTAURANT_ID)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["demo_mode"] = True
        context["show_demo_splash"] = True
        context["hide_restaurant_label"] = True
        context["demo_payload"] = {
            "concepts": [],
            "buffers": {},
            "favorites": {"concept_ids": [], "dish_ids": []},
        }
        context["restaurant_settings"] = SimpleNamespace(classic_creative_slider=50)
        context["update_creativity_url"] = "#"
        return context


# --- Healthcheck ---
class HealthView(View):
    def get(self, request):
        return JsonResponse({"ok": True, "app": "swipe"})


# --- APIs ---
class SwipeConceptBatchView(View):
    """
    Returns 3 fully generated concepts (each with 3 dishes)
    using existing llm helpers (OpenAI + Replicate).
    """

    def get(self, request):
        limit = int(request.GET.get("limit", 3))
        offset = int(request.GET.get("offset", 0))
        restaurant_context = request.GET.get("restaurant", "modern bistro")

        results = []
        for i in range(limit):
            name = f"Concept {offset + i + 1}"
            subtitle = random.choice([
                "Seasonal elegance", "Casual craft", "Woodland warmth", "Coastal simplicity"
            ])

            # 1️⃣ Generate sketch with your existing Replicate function
            try:
                sketch_url = llm.generate_concept_sketch({
                    "name": name,
                    "subtitle": subtitle,
                    "context": restaurant_context,
                })
            except Exception as e:
                sketch_url = getattr(llm, "DEFAULT_CONCEPT_IMAGE_URL", None)

            # 2️⃣ Generate dishes (3 per concept)
            dishes = []
            for j in range(3):
                dish_name = f"Dish {j+1} of {name}"
                desc = f"A creative dish inspired by {subtitle.lower()}."
                try:
                    image_url = llm.generate_dish_image_from_details(dish_name, desc)
                except Exception:
                    image_url = getattr(llm, "DEFAULT_DISH_IMAGE_URL", None)

                # Optional price enhancement via your LLM
                try:
                    enhanced = llm.enhance_dish(dish_name, desc)
                    price = enhanced.get("price", f"${random.randint(14, 34)}")
                except Exception:
                    price = f"${random.randint(14, 34)}"

                dishes.append({
                    "name": dish_name,
                    "description": desc,
                    "image": image_url,
                    "price": price,
                    "ingredients": random.sample(
                        ["thyme", "mushroom", "rosemary", "lemon", "garlic", "onion"], 3
                    ),
                    "reasoning": "A thoughtful balance of flavor and texture.",
                })

            # 3️⃣ Concept metadata
            meta = {
                "reasoning": f"A concept inspired by {subtitle.lower()} dining.",
                "ingredients": list({ing for d in dishes for ing in d["ingredients"]}),
            }

            results.append({
                "name": name,
                "subtitle": subtitle,
                "sketch_url": sketch_url,
                "meta": meta,
                "dishes": dishes,
            })

        return JsonResponse({"results": results, "limit": limit, "offset": offset})


class ConceptDishAppendView(View):
    """Append freshly generated dishes to an existing concept."""

    def post(self, request, concept_id):
        concept = get_object_or_404(
            Concept.objects.select_related("restaurant").filter(is_deleted=False),
            pk=concept_id,
        )

        restaurant_context = concept.restaurant.context
        generator = GetConcepts(
            restaurant=concept.restaurant,
            restaurant_context=restaurant_context,
        )

        try:
            saved_dishes = asyncio.run(generator.append_dishes_to_concept(concept))
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception(
                "Failed to append dishes for concept %s: %s", concept_id, exc
            )
            return JsonResponse(
                {"error": "Unable to fetch additional dishes."}, status=500
            )

        response_payload = []
        for dish in saved_dishes:
            ingredients = dish.get("ingredient_overlap") or dish.get("ingredients") or []
            if isinstance(ingredients, str):
                ingredients = [ingredients]

            dish_id = dish.get("id")
            concept_pk = dish.get("concept_id") or concept.id
            try:
                concept_pk_int = int(concept_pk)
            except (TypeError, ValueError):
                concept_pk_int = concept_pk

            variation_endpoint = ""
            if dish_id:
                variation_endpoint = reverse("swipe:dish_variation", args=[dish_id])

            response_payload.append(
                {
                    "id": dish_id,
                    "concept_id": concept_pk_int,
                    "name": dish.get("title") or dish.get("name") or "",
                    "reasoning": dish.get("description")
                    or dish.get("reasoning")
                    or "",
                    "ingredients": ingredients,
                    "price": dish.get("suggested_price") or dish.get("price") or "",
                    "image_url": dish.get("image_url") or "",
                    "is_seen": dish.get("is_seen", False),
                    "variation_endpoint": variation_endpoint,
                }
            )

        return JsonResponse({"dishes": response_payload})


class DishVariationView(View):
    """Generate a new variation for an existing dish."""

    def post(self, request, dish_id: int):
        dish = get_object_or_404(
            Dish.objects.select_related("concept__restaurant").filter(
                is_deleted=False,
                concept__is_deleted=False,
            ),
            pk=dish_id,
        )

        concept = dish.concept
        restaurant = concept.restaurant
        generator = GetConcepts(
            restaurant=restaurant,
            restaurant_context=restaurant.context,
        )

        try:
            saved_payload = asyncio.run(generator.generate_dish_variation(dish))
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Failed to generate variation for dish %s: %s", dish_id, exc)
            return JsonResponse(
                {"error": "Unable to generate dish variation."},
                status=500,
            )

        if not saved_payload:
            return JsonResponse(
                {"error": "No variation generated."},
                status=500,
            )

        ingredients = (
            saved_payload.get("ingredient_overlap")
            or saved_payload.get("ingredients")
            or []
        )
        if isinstance(ingredients, str):
            ingredients = [ingredients]

        dish_id_value = saved_payload.get("id")
        concept_id_value = saved_payload.get("concept_id") or concept.id
        try:
            concept_id_int = int(concept_id_value)
        except (TypeError, ValueError):
            concept_id_int = concept_id_value

        variation_endpoint = ""
        if dish_id_value:
            variation_endpoint = reverse("swipe:dish_variation", args=[dish_id_value])

        response_payload = {
            "id": dish_id_value,
            "concept_id": concept_id_int,
            "name": saved_payload.get("title") or saved_payload.get("name") or "",
            "reasoning": saved_payload.get("description")
            or saved_payload.get("reasoning")
            or "",
            "ingredients": ingredients,
            "price": saved_payload.get("suggested_price")
            or saved_payload.get("price")
            or "",
            "image_url": saved_payload.get("image_url") or "",
            "is_seen": saved_payload.get("is_seen", False),
            "variation_endpoint": variation_endpoint,
        }

        return JsonResponse({"dish": response_payload})


@method_decorator(csrf_exempt, name="dispatch")
class ToggleFavoriteAPI(View):
    """
    POST: { type: 'concept'|'dish', id: <int> }
    """

    def post(self, request):
        import json

        from .models import Concept, Dish

        try:
            payload = json.loads(request.body.decode("utf-8"))
            type_ = payload.get("type")
            id_ = int(payload.get("id"))
        except Exception:
            return HttpResponseBadRequest("Invalid payload")

        if type_ == "concept":
            try:
                concept = Concept.objects.get(id=id_, is_deleted=False)
                concept.is_favorite = not concept.is_favorite
                concept.save()
                return JsonResponse({"favorited": concept.is_favorite})
            except Concept.DoesNotExist:
                return HttpResponseBadRequest("Concept not found")
        elif type_ == "dish":
            try:
                dish = Dish.objects.get(id=id_, is_deleted=False, concept__is_deleted=False)
                dish.is_favorite = not dish.is_favorite
                dish.save()
                return JsonResponse({"favorited": dish.is_favorite})
            except Dish.DoesNotExist:
                return HttpResponseBadRequest("Dish not found")
        else:
            return HttpResponseBadRequest("Unknown type")


class DeleteCardAPI(LoginRequiredMixin, View):
    def post(self, request):
        import json

        body = request.body.decode("utf-8").strip()
        payload = {}

        if body:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = QueryDict(body).dict()

        if not payload:
            payload = request.POST.dict()

        if not payload:
            return HttpResponseBadRequest("Invalid payload")

        type_ = payload.get("type")
        item_id = payload.get("id")

        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Invalid item id")

        if type_ == "concept":
            concept = (
                Concept.objects.filter(id=item_id, is_deleted=False)
                .prefetch_related(
                    Prefetch(
                        "dishes",
                        queryset=Dish.objects.filter(is_deleted=False),
                    )
                )
                .first()
            )
            if concept is None:
                return HttpResponseBadRequest("Concept not found")

            dish_ids = list(dish.id for dish in concept.dishes.all())
            concept.is_deleted = True
            concept.is_favorite = False
            concept.save(update_fields=["is_deleted", "is_favorite"])
            concept.dishes.update(is_deleted=True, is_favorite=False)

            return JsonResponse(
                {
                    "deleted": True,
                    "type": "concept",
                    "id": concept.id,
                    "removed_dish_ids": dish_ids,
                }
            )

        if type_ == "dish":
            dish = (
                Dish.objects.select_related("concept")
                .filter(
                    id=item_id,
                    is_deleted=False,
                    concept__is_deleted=False,
                )
                .first()
            )
            if dish is None:
                return HttpResponseBadRequest("Dish not found")

            dish.is_deleted = True
            dish.is_favorite = False
            dish.save(update_fields=["is_deleted", "is_favorite"])

            return JsonResponse(
                {
                    "deleted": True,
                    "type": "dish",
                    "id": dish.id,
                    "concept_id": dish.concept_id,
                }
            )

        return HttpResponseBadRequest("Unknown type")


class FavoritesView(TemplateView):
    template_name = "swipe/favorites.html"

    def get_restaurant(self):
        restaurant_id = self.kwargs.get("restaurant_id") or self.request.GET.get("restaurant_id")
        if restaurant_id:
            try:
                return get_object_or_404(Restaurant, id=restaurant_id)
            except (TypeError, ValueError):
                logger.warning("Invalid restaurant_id supplied: %s", restaurant_id)
        return Restaurant.objects.order_by("-created_at").first()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        restaurant = self.get_restaurant()

        favorite_concepts = []
        all_favorite_dishes = []
        concept_groups = []

        if restaurant:
            favorite_concepts = list(
                Concept.objects.filter(
                    restaurant=restaurant,
                    is_favorite=True,
                    is_deleted=False,
                )
                .order_by("-created_at")
                .prefetch_related(
                    Prefetch(
                        "dishes",
                        queryset=Dish.objects.filter(is_deleted=False),
                    )
                )
            )

            all_favorite_dishes = list(
                Dish.objects.filter(
                    concept__restaurant=restaurant,
                    is_favorite=True,
                    is_deleted=False,
                    concept__is_deleted=False,
                )
                .select_related("concept")
                .order_by("-id")
            )

            concept_groups = []
            concept_lookup = {}

            for concept in favorite_concepts:
                group = {"concept": concept, "dishes": []}
                concept_groups.append(group)
                concept_lookup[concept.id] = group

            for dish in all_favorite_dishes:
                group = concept_lookup.get(dish.concept_id)
                if group is None:
                    group = {"concept": dish.concept, "dishes": []}
                    concept_groups.append(group)
                    concept_lookup[dish.concept_id] = group
                group["dishes"].append(dish)

        context.update({
            "restaurant": restaurant,
            "favorite_concepts": favorite_concepts,
            "all_favorite_dishes": all_favorite_dishes,
            "favorite_concept_groups": concept_groups,
        })
        return context


class DemoFavoritesView(TemplateView):
    template_name = "swipe/demo_favorites.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        demo_state = build_demo_state()

        concept_groups = []
        dish_lookup = {}
        for dish in demo_state.favorite_dishes:
            dish_lookup.setdefault(dish.concept_id, []).append(dish)

        for concept in demo_state.favorite_concepts:
            concept_groups.append({"concept": concept, "dishes": dish_lookup.get(concept.id, [])})

        restaurant = SimpleNamespace(name=demo_state.restaurant_name)

        context.update(
            {
                "demo_mode": True,
                "restaurant": restaurant,
                "favorite_concepts": demo_state.favorite_concepts,
                "all_favorite_dishes": demo_state.favorite_dishes,
                "favorite_concept_groups": concept_groups,
                "demo_payload": demo_state.as_payload(),
            }
        )
        return context


class SettingsView(TemplateView):
    template_name = "swipe/settings.html"

    def get_restaurant(self):
        restaurant_id = self.kwargs.get("restaurant_id") or self.request.GET.get("restaurant_id")
        if restaurant_id:
            try:
                return get_object_or_404(Restaurant, id=restaurant_id)
            except (TypeError, ValueError):
                logger.warning("Invalid restaurant_id supplied: %s", restaurant_id)
        return Restaurant.objects.order_by("-created_at").first()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        restaurant = self.get_restaurant()
        restaurant_settings = _resolve_restaurant_settings(restaurant)
        update_creativity_url = (
            reverse("update_creativity", args=[restaurant.id]) if restaurant else "#"
        )
        context.update(
            {
                "restaurant": restaurant,
                "restaurant_settings": restaurant_settings,
                "update_creativity_url": update_creativity_url,
            }
        )
        return context


class DemoSettingsView(TemplateView):
    template_name = "swipe/settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        restaurant = SimpleNamespace(
            name="Appertivo Demo",
            location_text="Anywhere, USA",
            phone="(555) 010-2024",
            website="https://appertivo.com",
            google_place_id=None,
            updated_at=None,
        )

        context.update(
            {
                "demo_mode": True,
                "restaurant": restaurant,
                "restaurant_settings": SimpleNamespace(classic_creative_slider=50),
                "update_creativity_url": "#",
            }
        )
        return context


class MarkSeenAPI(View):
    def post(self, request):
        import json

        body = request.body.decode("utf-8").strip()
        payload = {}

        if body:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = QueryDict(body).dict()

        if not payload:
            payload = request.POST.dict()

        if not payload:
            return HttpResponseBadRequest("Invalid payload")

        type_ = payload.get("type")
        item_id = payload.get("id")

        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Invalid item id")

        if type_ == "concept":
            concept = (
                Concept.objects.filter(id=item_id, is_deleted=False)
                .first()
            )
            if concept is None:
                return HttpResponseBadRequest("Unknown concept")
            if not concept.is_seen:
                concept.is_seen = True
                concept.save(update_fields=["is_seen"])
            return HttpResponse("")

        if type_ == "dish":
            dish = (
                Dish.objects.select_related("concept")
                .filter(id=item_id, is_deleted=False, concept__is_deleted=False)
                .first()
            )
            if dish is None:
                return HttpResponseBadRequest("Unknown dish")
            if not dish.is_seen:
                dish.is_seen = True
                dish.save(update_fields=["is_seen"])
            return HttpResponse("")

        return HttpResponseBadRequest("Unknown type")
