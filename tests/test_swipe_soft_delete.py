import json

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from app.models import Account, Membership, Onboarding, Restaurant
from swipe.models import Concept, Dish
from swipe.views import FavoritesView, SwipeHomeView


class SwipeSoftDeleteTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Test Account")
        self.restaurant = Restaurant.objects.create(
            account=self.account,
            name="Test Restaurant",
            location_text="123 Anywhere",
        )
        self.concept = Concept.objects.create(
            restaurant=self.restaurant,
            name="Concept Alpha",
            subtitle="Original",
            meta_ingredients=["a", "b"],
            meta_reasoning="Reason",
        )
        self.dish_active = Dish.objects.create(
            concept=self.concept,
            name="Dish Active",
            reasoning="Tasty",
            ingredients=["x"],
        )
        self.dish_deleted = Dish.objects.create(
            concept=self.concept,
            name="Dish Deleted",
            reasoning="Hidden",
            ingredients=["y"],
            is_deleted=True,
        )
        self.user = get_user_model().objects.create_user(
            username="tester", email="tester@example.com", password="pass"
        )
        Membership.objects.create(account=self.account, user=self.user)
        Onboarding.objects.create(
            user=self.user,
            restaurant=self.restaurant,
            state=Onboarding.State.COMPLETE,
        )
        self.factory = RequestFactory()

    def _home_context(self):
        request = self.factory.get(reverse("swipe:home"))
        request.user = self.user
        response = SwipeHomeView.as_view()(request)
        response.render()
        return response.context_data

    def _favorites_context(self):
        request = self.factory.get(reverse("swipe:favorites"))
        request.user = self.user
        response = FavoritesView.as_view()(request)
        response.render()
        return response.context_data

    def test_defaults_not_deleted(self):
        fresh_concept = Concept.objects.create(
            restaurant=self.restaurant,
            name="Concept Beta",
        )
        fresh_dish = Dish.objects.create(concept=fresh_concept, name="Dish Beta")
        self.assertFalse(fresh_concept.is_deleted)
        self.assertFalse(fresh_dish.is_deleted)

    def test_swipe_home_excludes_deleted_records(self):
        Concept.objects.create(
            restaurant=self.restaurant,
            name="Concept Hidden",
            is_deleted=True,
        )
        context = self._home_context()
        concepts = context["concepts"]
        self.assertEqual(len(concepts), 1)
        concept = concepts[0]
        self.assertEqual(concept.id, self.concept.id)
        self.assertTrue(all(not dish.is_deleted for dish in concept.dishes.all()))
        dish_counts = context["dish_counts"]
        self.assertEqual(dish_counts, [1])

    def test_favorites_view_excludes_deleted_items(self):
        self.concept.is_favorite = True
        self.concept.save()
        self.dish_active.is_favorite = True
        self.dish_active.save()
        Dish.objects.create(
            concept=self.concept,
            name="Dish Favorite Deleted",
            is_favorite=True,
            is_deleted=True,
        )

        context = self._favorites_context()
        favorite_concepts = context["favorite_concepts"]
        all_favorite_dishes = context["all_favorite_dishes"]
        self.assertEqual([c.id for c in favorite_concepts], [self.concept.id])
        self.assertEqual([d.id for d in all_favorite_dishes], [self.dish_active.id])

    def test_delete_dish_api_marks_deleted(self):
        self.client.force_login(self.user)
        self.dish_active.is_favorite = True
        self.dish_active.save()

        url = reverse("swipe:delete_card")
        response = self.client.post(
            url,
            data=json.dumps({"type": "dish", "id": self.dish_active.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["deleted"])
        self.assertEqual(payload["concept_id"], self.concept.id)

        self.dish_active.refresh_from_db()
        self.assertTrue(self.dish_active.is_deleted)
        self.assertFalse(self.dish_active.is_favorite)

    def test_delete_concept_api_soft_deletes_children(self):
        self.client.force_login(self.user)
        second_dish = Dish.objects.create(
            concept=self.concept,
            name="Dish Extra",
            ingredients=["z"],
            is_favorite=True,
        )
        self.concept.is_favorite = True
        self.concept.save()

        url = reverse("swipe:delete_card")
        response = self.client.post(
            url,
            data=json.dumps({"type": "concept", "id": self.concept.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["deleted"])
        self.assertCountEqual(
            payload["removed_dish_ids"], [self.dish_active.id, second_dish.id]
        )

        self.concept.refresh_from_db()
        self.assertTrue(self.concept.is_deleted)
        self.assertFalse(self.concept.is_favorite)
        self.assertTrue(
            Dish.objects.filter(
                concept=self.concept, is_deleted=True, is_favorite=False
            ).count()
            >= 2
        )

    def test_toggle_favorite_rejects_deleted(self):
        self.concept.is_deleted = True
        self.concept.save()
        url = reverse("swipe:api_favorite")
        response = self.client.post(
            url,
            data=json.dumps({"type": "concept", "id": self.concept.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

        self.dish_active.is_deleted = True
        self.dish_active.save()
        response = self.client.post(
            url,
            data=json.dumps({"type": "dish", "id": self.dish_active.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_mark_seen_rejects_deleted(self):
        self.client.force_login(self.user)
        mark_seen_url = reverse("swipe:mark_seen")

        self.concept.is_deleted = True
        self.concept.save()
        response = self.client.post(
            mark_seen_url,
            data=json.dumps({"type": "concept", "id": self.concept.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

        self.concept.is_deleted = False
        self.concept.save()
        self.dish_active.is_deleted = True
        self.dish_active.save()
        response = self.client.post(
            mark_seen_url,
            data=json.dumps({"type": "dish", "id": self.dish_active.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_append_dishes_rejected_for_deleted_concept(self):
        self.concept.is_deleted = True
        self.concept.save()
        url = reverse("swipe:concept_append_dishes", args=[self.concept.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)
