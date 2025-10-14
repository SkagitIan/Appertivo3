import asyncio
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseBadRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView, View
from app.llm import *
from app.models import Restaurant
# Stub imports for future service integration
# from .services import generate_concepts_batch  # to be implemented
from swipe.llm_utils import GetConcepts
from swipe.models import Concept, SeenItem
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
        generator = GetConcepts(restaurant=restaurant)

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
                Concept.objects.filter(restaurant=restaurant)
                .order_by("-created_at")
                .prefetch_related("dishes")
            )
            concepts = list(concept_qs)

        user = self.request.user
        seen_concept_ids = set()
        seen_dish_ids = set()

        if user.is_authenticated and concepts:
            concept_ids = [concept.id for concept in concepts]
            seen_concept_ids = set(
                SeenItem.objects.filter(
                    user=user,
                    item_type=SeenItem.ItemType.CONCEPT,
                    item_id__in=concept_ids,
                ).values_list("item_id", flat=True)
            )

            dish_ids = []
            for concept in concepts:
                dish_ids.extend(dish.id for dish in concept.dishes.all())

            if dish_ids:
                seen_dish_ids = set(
                    SeenItem.objects.filter(
                        user=user,
                        item_type=SeenItem.ItemType.DISH,
                        item_id__in=dish_ids,
                    ).values_list("item_id", flat=True)
                )

        for concept in concepts:
            concept.is_new = user.is_authenticated and concept.id not in seen_concept_ids
            for dish in concept.dishes.all():
                dish.is_new = user.is_authenticated and dish.id not in seen_dish_ids

        dish_counts = [len(c.dishes.all()) for c in concepts]

        context.update(
            {
                "restaurant": restaurant,
                "concepts": concepts,
                "dish_counts": dish_counts,
            }
        )
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


@method_decorator(csrf_exempt, name="dispatch")
class ToggleFavoriteAPI(LoginRequiredMixin, View):
    """
    POST: { type: 'concept'|'dish', id: <int> }
    """

    def post(self, request):
        import json

        from .models import Concept, Dish, Favorite

        try:
            payload = json.loads(request.body.decode("utf-8"))
            type_ = payload.get("type")
            id_ = int(payload.get("id"))
        except Exception:
            return HttpResponseBadRequest("Invalid payload")

        fav_kwargs = {"user": request.user}
        if type_ == "concept":
            fav_kwargs["concept_id"] = id_
        elif type_ == "dish":
            fav_kwargs["dish_id"] = id_
        else:
            return HttpResponseBadRequest("Unknown type")

        fav, created = Favorite.objects.get_or_create(**fav_kwargs)
        if not created:
            fav.delete()
            return JsonResponse({"favorited": False})
        return JsonResponse({"favorited": True})


class MarkSeenAPI(LoginRequiredMixin, View):
    def post(self, request):
        import json

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON payload")

        type_ = payload.get("type")
        item_id = payload.get("id")

        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Invalid item id")

        if type_ == SeenItem.ItemType.CONCEPT:
            if not Concept.objects.filter(id=item_id).exists():
                return HttpResponseBadRequest("Unknown concept")
            SeenItem.objects.get_or_create(
                user=request.user,
                item_type=SeenItem.ItemType.CONCEPT,
                item_id=item_id,
            )
        elif type_ == SeenItem.ItemType.DISH:
            from swipe.models import Dish

            if not Dish.objects.filter(id=item_id).exists():
                return HttpResponseBadRequest("Unknown dish")
            SeenItem.objects.get_or_create(
                user=request.user,
                item_type=SeenItem.ItemType.DISH,
                item_id=item_id,
            )
        else:
            return HttpResponseBadRequest("Unknown type")

        return JsonResponse({"seen": True})
