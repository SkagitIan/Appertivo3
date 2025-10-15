from unittest.mock import patch

from asgiref.sync import sync_to_async
from django.test import TransactionTestCase
from django.urls import reverse

from app.models import Account, Restaurant
from swipe.llm_utils import GetConcepts
from swipe.models import Concept, Dish


class ConceptAppendDishesAPITests(TransactionTestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Account")
        self.restaurant = Restaurant.objects.create(
            account=self.account,
            name="Test Restaurant",
            location_text="Test City",
        )
        self.concept = Concept.objects.create(
            restaurant=self.restaurant,
            name="Seaside Evenings",
            subtitle="Coastal comfort",
            meta_ingredients=["citrus", "herb"],
            meta_reasoning="Inspired by shoreline suppers.",
        )
        Dish.objects.create(
            concept=self.concept,
            name="Existing Dish",
            reasoning="Starter",
            ingredients=["lemon"],
            price="$10",
            image_url="https://example.com/existing.jpg",
        )

    def test_append_dishes_returns_payload_and_updates_count(self):
        concept = self.concept
        new_dish_payloads = [
            {
                "title": "Grilled Octopus",
                "description": "Charred octopus with smoked paprika aioli.",
                "ingredient_overlap": ["octopus", "paprika", "citrus"],
                "suggested_price": "$24",
                "image_url": "https://example.com/octopus.jpg",
            },
            {
                "title": "Citrus Panna Cotta",
                "description": "Silky panna cotta with orange reduction.",
                "ingredient_overlap": ["cream", "orange", "thyme"],
                "suggested_price": "$12",
                "image_url": "https://example.com/pannacotta.jpg",
            },
        ]

        test_self = self

        async def fake_append(_generator, concept_arg):  # pragma: no cover - helper closure
            test_self.assertEqual(concept_arg.id, concept.id)

            def create():
                dishes = []
                for payload in new_dish_payloads:
                    dishes.append(
                        Dish.objects.create(
                            concept=concept,
                            name=payload["title"],
                            reasoning=payload["description"],
                            ingredients=payload["ingredient_overlap"],
                            price=payload["suggested_price"],
                            image_url=payload["image_url"],
                        )
                    )
                return dishes

            created_dishes = await sync_to_async(create)()
            return [
                {
                    "id": dish.id,
                    "concept_id": dish.concept_id,
                    "title": dish.name,
                    "description": dish.reasoning,
                    "ingredient_overlap": dish.ingredients,
                    "suggested_price": dish.price,
                    "image_url": dish.image_url,
                }
                for dish in created_dishes
            ]

        url = reverse("swipe:concept_append_dishes", args=[concept.id])

        with patch.object(GetConcepts, "_load_locale", lambda *_: None):
            with patch.object(GetConcepts, "append_dishes_to_concept", fake_append):
                response = self.client.post(url)

        self.assertEqual(response.status_code, 200)

        payload = response.json()
        dishes = payload.get("dishes", [])
        self.assertEqual(len(dishes), len(new_dish_payloads))

        for dish_payload, source in zip(dishes, new_dish_payloads):
            self.assertEqual(
                set(dish_payload.keys()),
                {"id", "name", "reasoning", "ingredients", "price", "image_url"},
            )
            self.assertEqual(dish_payload["name"], source["title"])
            self.assertEqual(dish_payload["reasoning"], source["description"])
            self.assertEqual(dish_payload["ingredients"], source["ingredient_overlap"])
            self.assertEqual(dish_payload["price"], source["suggested_price"])
            self.assertEqual(dish_payload["image_url"], source["image_url"])

        total_dishes = Dish.objects.filter(concept=concept).count()
        self.assertEqual(total_dishes, 1 + len(new_dish_payloads))
