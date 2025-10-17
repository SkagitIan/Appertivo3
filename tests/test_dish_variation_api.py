from django.test import TransactionTestCase
from django.urls import reverse

from app.models import Account, Restaurant
from swipe.models import Concept, Dish


class DishVariationAPITests(TransactionTestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Account")
        self.restaurant = Restaurant.objects.create(
            account=self.account,
            name="Variation Test Restaurant",
            location_text="Test City",
        )
        self.concept = Concept.objects.create(
            restaurant=self.restaurant,
            name="Garden Evenings",
            subtitle="Herbal comfort",
            meta_ingredients=["herb"],
            meta_reasoning="Fresh herb-forward dishes.",
        )
        self.dish = Dish.objects.create(
            concept=self.concept,
            name="Herb Roast",
            reasoning="Roasted poultry with garden herbs.",
            ingredients=["thyme", "rosemary", "garlic"],
            price="$18",
            image_url="https://example.com/herb.jpg",
        )

    def test_variation_endpoint_creates_dish_and_returns_payload(self):
        url = reverse("swipe:dish_variation", args=[self.dish.id])
        initial_count = Dish.objects.filter(concept=self.concept).count()

        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        dish_payload = payload.get("dish")
        self.assertIsInstance(dish_payload, dict)

        self.assertEqual(
            set(dish_payload.keys()),
            {
                "id",
                "concept_id",
                "name",
                "reasoning",
                "ingredients",
                "price",
                "image_url",
                "is_seen",
                "variation_endpoint",
            },
        )
        self.assertEqual(dish_payload["concept_id"], self.concept.id)
        self.assertFalse(dish_payload["is_seen"])
        self.assertTrue(dish_payload["name"])
        self.assertTrue(dish_payload["reasoning"])
        self.assertTrue(dish_payload["price"])
        self.assertTrue(dish_payload["image_url"])
        self.assertTrue(dish_payload["variation_endpoint"])
        self.assertIsInstance(dish_payload["ingredients"], list)
        self.assertTrue(dish_payload["ingredients"])
        self.assertEqual(
            dish_payload["variation_endpoint"],
            reverse("swipe:dish_variation", args=[dish_payload["id"]]),
        )

        new_count = Dish.objects.filter(concept=self.concept).count()
        self.assertEqual(new_count, initial_count + 1)
        self.assertTrue(Dish.objects.filter(pk=dish_payload["id"]).exists())
