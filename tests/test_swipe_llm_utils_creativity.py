import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase, TestCase

from app import models
from swipe.models import Concept, Dish
from swipe.llm_utils import GetConcepts


def _make_restaurant_namespace(slider_value: int) -> SimpleNamespace:
    restaurant = SimpleNamespace(
        id=1,
        name="Test Bistro",
        location_text="Testville",
    )
    restaurant.restaurantsettings = SimpleNamespace(classic_creative_slider=slider_value)
    return restaurant


class CreativityPromptUnitTests(SimpleTestCase):
    def test_concept_prompt_includes_creativity_statement(self):
        restaurant = _make_restaurant_namespace(75)

        with patch.object(GetConcepts, "_load_locale", AsyncMock(return_value=None)):
            helper = GetConcepts(restaurant=restaurant)

        helper.openai_client = SimpleNamespace(
            responses=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(
                        output=[
                            SimpleNamespace(
                                content=[
                                    SimpleNamespace(text=json.dumps({"concepts": []}))
                                ]
                            )
                        ]
                    )
                )
            )
        )

        with patch.object(helper, "_get_restaurant_context", AsyncMock(return_value="Context")):
            asyncio.run(helper._generate_concepts())

        call_args = helper.openai_client.responses.create.call_args
        system_prompt = call_args.kwargs["input"][0]["content"]

        self.assertEqual(helper.creativity_slider_raw, 75)
        self.assertEqual(helper.creativity_level, 8)
        self.assertIn("The user has chosen 8 on a 1–10 classic-to-creative scale.", system_prompt)

    def test_dish_prompt_includes_creativity_statement(self):
        restaurant = _make_restaurant_namespace(65)

        with patch.object(GetConcepts, "_load_locale", AsyncMock(return_value=None)):
            helper = GetConcepts(restaurant=restaurant)

        helper.openai_client = SimpleNamespace(
            responses=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(
                        output=[
                            SimpleNamespace(
                                content=[
                                    SimpleNamespace(text=json.dumps({"dishes": []}))
                                ]
                            )
                        ]
                    )
                )
            )
        )

        concept_payload = {
            "title": "Cozy Night",
            "subtitle": "Comfort classics",
            "reasoning": "Comfort focus",
            "tags": ["comfort"],
        }

        with patch.object(helper, "_get_restaurant_context", AsyncMock(return_value="Context")):
            asyncio.run(helper._generate_dishes_for_concept(concept_payload))

        call_args = helper.openai_client.responses.create.call_args
        dish_prompt = call_args.kwargs["input"]

        self.assertEqual(helper.creativity_slider_raw, 65)
        self.assertEqual(helper.creativity_level, 7)
        self.assertIn("The user has chosen 7 on a 1–10 classic-to-creative scale.", dish_prompt)


class CreativityPromptIntegrationTests(TestCase):
    def test_dish_variation_prompt_includes_creativity_statement(self):
        account = models.Account.objects.create(name="Account")
        restaurant = models.Restaurant.objects.create(
            account=account,
            name="Restaurant",
            location_text="City",
        )
        models.RestaurantSettings.objects.create(
            restaurant=restaurant,
            classic_creative_slider=100,
        )

        concept = Concept.objects.create(
            restaurant=restaurant,
            name="Seasonal Concept",
            subtitle="",
        )

        dish = Dish.objects.create(
            concept=concept,
            name="Autumn Roast",
            reasoning="Rich and cozy.",
            ingredients=["squash", "sage"],
        )

        with patch.object(GetConcepts, "_load_locale", AsyncMock(return_value=None)):
            helper = GetConcepts(restaurant=restaurant)

        helper.openai_client = SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(output=[])))
        )
        helper._save_dishes = AsyncMock(return_value=[{"id": str(dish.id)}])
        helper._get_restaurant_context = AsyncMock(return_value="Context")

        asyncio.run(helper.generate_dish_variation(dish))

        call_args = helper.openai_client.responses.create.call_args
        variation_prompt = call_args.kwargs["input"]

        self.assertEqual(helper.creativity_slider_raw, 100)
        self.assertEqual(helper.creativity_level, 10)
        self.assertIn("The user has chosen 10 on a 1–10 classic-to-creative scale.", variation_prompt)
