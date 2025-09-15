from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app import models


@override_settings(SECURE_SSL_REDIRECT=False)
class ViewSmokeTests(TestCase):
    """Ensure newly added views respond correctly."""

    def setUp(self):
        self.user = User.objects.create_user("u@example.com", password="pw")
        self.account = models.Account.objects.create(name="Acc")
        models.Membership.objects.create(account=self.account, user=self.user)
        self.restaurant = models.Restaurant.objects.create(
            account=self.account, name="R", location_text="City"
        )
        models.RestaurantSettings.objects.create(restaurant=self.restaurant)
        self.client.login(username="u@example.com", password="pw")

    def _create_concepts(self):
        run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.CONCEPTS,
            model_name="m",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        for i in range(9):
            models.Concept.objects.create(
                restaurant=self.restaurant,
                ideation_run=run,
                name=f"C{i}",
                rank_order=i,
            )

    def _create_dishes(self, concept):
        run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.DISHES,
            model_name="m",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            parent_concept=concept,
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        for i in range(9):
            models.DishIdea.objects.create(
                restaurant=self.restaurant,
                ideation_run=run,
                parent_concept=concept,
                title=f"D{i}",
                description="desc",
                ingredient_names=[],
                category_tags=[],
            )

    def test_public_pages(self):
        for name in ["home", "signup", "login"]:
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200)

    def test_signup_creates_user(self):
        self.client.logout()
        data = {
            "email": "new@example.com",
            "password": "pw",
            "restaurant_name": "N",
            "location": "Town",
        }
        resp = self.client.post(reverse("signup"), data)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(User.objects.filter(username="new@example.com").exists())

    def test_login_authenticates(self):
        self.client.logout()
        resp = self.client.post(
            reverse("login"), {"username": "u@example.com", "password": "pw"}
        )
        self.assertEqual(resp.status_code, 302)

    def test_onboarding_views(self):
        resp = self.client.get(reverse("onboarding"))
        self.assertEqual(resp.status_code, 200)
        status_resp = self.client.get(reverse("onboarding-status"))
        self.assertEqual(status_resp.json()["status"], "pending")

    def test_manual_menu(self):
        resp = self.client.get(reverse("manual-menu"))
        self.assertEqual(resp.status_code, 200)

    def test_concepts_and_generation(self):
        self._create_concepts()
        resp = self.client.get(reverse("concepts"))
        self.assertContains(resp, "C0")
        gen_resp = self.client.post(reverse("concepts-generate"))
        self.assertEqual(gen_resp.status_code, 200)

    def test_concept_favorite(self):
        self._create_concepts()
        concept = models.Concept.objects.first()
        resp = self.client.post(reverse("concept-favorite", args=[concept.id]))
        self.assertTrue(resp.json()["favorited"])

    def test_dishes_and_generation(self):
        self._create_concepts()
        concept = models.Concept.objects.first()
        self._create_dishes(concept)
        resp = self.client.get(reverse("dishes", args=[concept.id]))
        self.assertContains(resp, "D0")
        gen_resp = self.client.post(reverse("dish-generate", args=[concept.id]))
        self.assertEqual(gen_resp.status_code, 200)

    def test_dish_favorite_and_variation(self):
        self._create_concepts()
        concept = models.Concept.objects.first()
        self._create_dishes(concept)
        dish = models.DishIdea.objects.first()
        fav_resp = self.client.post(reverse("dish-favorite", args=[dish.id]))
        self.assertTrue(fav_resp.json()["favorited"])
        var_resp = self.client.post(reverse("dish-variation", args=[dish.id]))
        self.assertEqual(var_resp.status_code, 200)

    def test_favorites_views(self):
        resp = self.client.get(reverse("favorites"))
        self.assertEqual(resp.status_code, 200)
        concept = models.Concept.objects.create(
            restaurant=self.restaurant,
            ideation_run=models.IdeationRun.objects.create(
                restaurant=self.restaurant,
                initiated_by_user=self.user,
                type=models.IdeationRun.RunType.CONCEPTS,
                model_name="m",
                temperature=0,
                classic_creative=50,
                context_snapshot={},
                status=models.IdeationRun.Status.SUCCEEDED,
            ),
            name="Fav",
            rank_order=1,
        )
        models.FavoriteConcept.objects.create(
            user=self.user, concept=concept, favorited_at=timezone.now()
        )
        rem_resp = self.client.post(
            reverse("favorite-remove", args=["concept", concept.id])
        )
        self.assertEqual(rem_resp.status_code, 200)

    def test_menu_collection_and_item(self):
        create_resp = self.client.post(
            reverse("menu-collection-create"), {"name": "Menu"}
        )
        self.assertEqual(create_resp.status_code, 200)
        collection_id = create_resp.json()["id"]
        self._create_concepts()
        concept = models.Concept.objects.first()
        self._create_dishes(concept)
        dish = models.DishIdea.objects.first()
        add_resp = self.client.post(
            reverse("menu-item-add", args=[dish.id, collection_id])
        )
        self.assertEqual(add_resp.status_code, 200)

    def test_settings_views(self):
        resp = self.client.get(reverse("settings"))
        self.assertEqual(resp.status_code, 200)
        rescrape = self.client.post(reverse("settings-rescrape-menu"))
        self.assertEqual(rescrape.status_code, 200)
        slider = self.client.post(reverse("settings-slider-update"), {"value": 60})
        self.assertEqual(slider.json()["value"], 60)

    def test_billing_views(self):
        resp = self.client.get(reverse("billing"))
        self.assertEqual(resp.status_code, 200)
        up = self.client.post(reverse("billing-upgrade"))
        self.assertEqual(up.status_code, 200)
        cancel = self.client.post(reverse("billing-cancel"))
        self.assertEqual(cancel.status_code, 200)

    def test_job_status_and_notifications(self):
        job = models.Job.objects.create(
            account=self.account,
            restaurant=self.restaurant,
            user=self.user,
            kind=models.Job.Kind.IDEATION,
            ref_table="x",
            ref_id=self.restaurant.id,
            status=models.Job.Status.QUEUED,
            progress_pct=0,
        )
        job_resp = self.client.get(reverse("job-status", args=[job.id]))
        self.assertEqual(job_resp.json()["status"], "queued")
        notif_resp = self.client.get(reverse("notification-list"))
        self.assertEqual(notif_resp.status_code, 200)
