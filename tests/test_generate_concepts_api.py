from unittest.mock import patch

from asgiref.sync import sync_to_async

from django.test import TransactionTestCase
from django.urls import reverse

from app.models import Account, Restaurant
from swipe.llm_utils import GetConcepts
from swipe.models import Concept, Dish


class GenerateConceptsViewTests(TransactionTestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Account")
        self.restaurant = Restaurant.objects.create(
            account=self.account,
            name="Test Restaurant",
            location_text="Test City",
        )

    def test_response_includes_persisted_ids(self):
        concept_payload = {
            "title": "Harvest Evenings",
            "subtitle": "Autumn comfort classics",
            "reasoning": "Seasonal menu refresh",
            "tags": ["autumn", "comfort", "harvest"],
            "ideal_dishes": "Hearty mains and shared plates",
        }
        dish_payloads = [
            {
                "title": "Maple Glazed Pork",
                "description": "Glazed pork with roasted squash.",
                "ingredient_overlap": ["pork", "maple", "squash"],
                "category_tags": ["entree", "pork", "fall"],
                "suggested_price": "$24",
            },
            {
                "title": "Cider Poached Pear",
                "description": "Poached pear with spiced crumble.",
                "ingredient_overlap": ["pear", "cider", "spice"],
                "category_tags": ["dessert", "fruit", "fall"],
                "suggested_price": "$12",
            },
        ]

        async def fake_generate_concepts(_self):
            return [concept_payload]

        async def fake_generate_sketch(_self, _concept):
            return "https://example.com/concept.png"

        async def fake_generate_dishes_for_concept(_self, _concept):
            return dish_payloads

        async def fake_generate_image(_self, _dish):
            return "https://example.com/dish.png"

        async def immediate_to_thread(func, /, *args, **kwargs):
            bound = sync_to_async(func, thread_sensitive=True)
            return await bound(*args, **kwargs)

        url = reverse("swipe:generate_concepts", args=[self.restaurant.id])

        with (
            patch.object(GetConcepts, "_generate_concepts", fake_generate_concepts),
            patch.object(GetConcepts, "_generate_sketch", fake_generate_sketch),
            patch.object(
                GetConcepts,
                "_generate_dishes_for_concept",
                fake_generate_dishes_for_concept,
            ),
            patch.object(GetConcepts, "_generate_image", fake_generate_image),
            patch("swipe.llm_utils.asyncio.to_thread", immediate_to_thread),
        ):
            response = self.client.post(url)

        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assertEqual(payload["status"], "success")
        results = payload["results"]
        self.assertEqual(len(results), 1)

        concept_response = results[0]
        concept_obj = Concept.objects.get()
        expected_concept_id = (
            str(concept_obj.id)
            if hasattr(concept_obj.id, "hex")
            else concept_obj.id
        )
        self.assertEqual(concept_response["id"], expected_concept_id)
        self.assertIn("is_seen", concept_response)
        self.assertFalse(concept_response["is_seen"])

        dishes = concept_response["dishes"]
        stored_dishes = list(Dish.objects.filter(concept=concept_obj).order_by("id"))
        self.assertEqual(len(dishes), len(stored_dishes))

        for response_dish, stored_dish in zip(dishes, stored_dishes):
            expected_dish_id = (
                str(stored_dish.id)
                if hasattr(stored_dish.id, "hex")
                else stored_dish.id
            )
            expected_concept_fk = (
                str(stored_dish.concept_id)
                if hasattr(stored_dish.concept_id, "hex")
                else stored_dish.concept_id
            )
            self.assertEqual(response_dish["id"], expected_dish_id)
            self.assertEqual(response_dish["concept_id"], expected_concept_fk)
            self.assertIn("is_seen", response_dish)
            self.assertFalse(response_dish["is_seen"])

