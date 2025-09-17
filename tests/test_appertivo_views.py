from unittest.mock import patch

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
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            models.FavoriteConcept.objects.filter(user=self.user, concept=concept).exists()
        )
        self.assertIn("concept-background-loader", resp.content.decode())

    def test_concepts_page_marks_existing_favorites(self):
        self._create_concepts()
        concept = models.Concept.objects.first()
        models.FavoriteConcept.objects.create(
            user=self.user, concept=concept, favorited_at=timezone.now()
        )

        response = self.client.get(reverse("concepts"))
        self.assertContains(response, "★ Favorited")

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

    def test_dish_delete_removes_dish_and_assets(self):
        self._create_concepts()
        concept = models.Concept.objects.first()
        self._create_dishes(concept)
        dish = models.DishIdea.objects.first()

        asset = models.Asset.objects.create(
            kind=models.Asset.Kind.IMAGE,
            storage_key="test/key",
            public_url="https://example.com/image.jpg",
        )
        models.Enhancement.objects.create(
            dish=dish,
            triggered_by_user=self.user,
            status=models.Enhancement.Status.SUCCEEDED,
            image_asset=asset,
            model_name="test-model",
        )

        resp = self.client.post(reverse("dish-delete", args=[dish.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"")
        self.assertFalse(models.DishIdea.objects.filter(id=dish.id).exists())
        self.assertFalse(models.Asset.objects.filter(id=asset.id).exists())

    def test_dish_delete_requires_membership(self):
        other_account = models.Account.objects.create(name="Other")
        other_restaurant = models.Restaurant.objects.create(
            account=other_account, name="Other R", location_text="Town"
        )
        concept_run = models.IdeationRun.objects.create(
            restaurant=other_restaurant,
            initiated_by_user=None,
            type=models.IdeationRun.RunType.CONCEPTS,
            model_name="m",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        other_concept = models.Concept.objects.create(
            restaurant=other_restaurant,
            ideation_run=concept_run,
            name="Other Concept",
            subtitle="Sub",
            rank_order=0,
        )
        dish_run = models.IdeationRun.objects.create(
            restaurant=other_restaurant,
            initiated_by_user=None,
            type=models.IdeationRun.RunType.DISHES,
            model_name="m",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            parent_concept=other_concept,
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        other_dish = models.DishIdea.objects.create(
            restaurant=other_restaurant,
            ideation_run=dish_run,
            parent_concept=other_concept,
            title="Other Dish",
            description="Desc",
            ingredient_names=[],
            category_tags=[],
        )

        resp = self.client.post(reverse("dish-delete", args=[other_dish.id]))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(models.DishIdea.objects.filter(id=other_dish.id).exists())

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
        self.restaurant.context_json = {"story": "Farm-to-table"}
        self.restaurant.save(update_fields=["context_json"])
        menu_version = models.MenuVersion.objects.create(
            restaurant=self.restaurant,
            source_kind=models.MenuVersion.SourceKind.PASTED_TEXT,
            raw_markdown="Specials\n- Dish",
            status=models.MenuVersion.Status.SUCCEEDED,
        )
        self.restaurant.active_menu_version = menu_version
        self.restaurant.save(update_fields=["active_menu_version"])

        resp = self.client.get(reverse("settings"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Farm-to-table")
        self.assertContains(resp, "Specials")

        update_resp = self.client.post(
            reverse("update_restaurant_info"),
            {"menu_url": "https://example.com/new-menu"},
        )
        self.assertEqual(update_resp.status_code, 302)
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.primary_menu_url, "https://example.com/new-menu")

        self.restaurant.primary_menu_url = "https://example.com/menu"
        self.restaurant.save(update_fields=["primary_menu_url"])
        with patch("app.views.scrape_menu.delay") as mock_delay:
            rescrape = self.client.post(
                reverse("settings-rescrape-menu", args=[self.restaurant.id])
            )
        self.assertEqual(rescrape.status_code, 200)
        self.assertTrue(rescrape.json()["rescrape_complete"])
        mv = models.MenuVersion.objects.get(restaurant=self.restaurant, source_url="https://example.com/menu")
        self.assertEqual(mv.status, models.MenuVersion.Status.QUEUED)
        mock_delay.assert_called_once_with(str(mv.id))

        slider = self.client.post(
            reverse("update_creativity", args=[self.restaurant.id]),
            {"classic_creative_slider": 60},
        )
        self.assertEqual(slider.json()["status"], "ok")
        self.restaurant.refresh_from_db()
        self.assertEqual(
            self.restaurant.restaurantsettings.classic_creative_slider,
            60,
        )

    def test_dashboard_prompts_for_menu_when_missing(self):
        self.restaurant.primary_menu_url = None
        self.restaurant.save(update_fields=["primary_menu_url"])

        response = self.client.get(reverse("dashboard", args=[self.restaurant.id]))
        self.assertContains(response, "show_menu_modal")

    def test_dashboard_does_not_prompt_when_menu_exists(self):
        self.restaurant.primary_menu_url = "https://example.com/menu"
        self.restaurant.save(update_fields=["primary_menu_url"])

        response = self.client.get(reverse("dashboard", args=[self.restaurant.id]))
        self.assertNotIn("show_menu_modal", response.content.decode())

    def test_logout_via_get_redirects(self):
        response = self.client.get(reverse("logout"))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_rescrape_menu_requires_url(self):
        self.restaurant.primary_menu_url = None
        self.restaurant.save(update_fields=["primary_menu_url"])
        with patch("app.views.scrape_menu.delay") as mock_delay:
            resp = self.client.post(
                reverse("settings-rescrape-menu", args=[self.restaurant.id])
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "missing_menu_url")
        self.assertFalse(models.MenuVersion.objects.exists())
        mock_delay.assert_not_called()

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
