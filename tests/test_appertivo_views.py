import json
from datetime import timedelta
from types import SimpleNamespace
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app import models, onboarding, views


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STRIPE_PRICE_ID="price_test",
    STRIPE_SECRET_KEY="sk_test",
    STRIPE_TRIAL_DAYS=14,
)
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
        self.assertEqual(resp["Location"], reverse("onboarding"))
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
        self.assertContains(resp, "Track onboarding progress")
        status_resp = self.client.get(
            reverse("onboarding-status"), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(status_resp.status_code, 200)
        self.assertIn("Current state", status_resp.content.decode())

    def test_billing_upgrade_starts_checkout(self):
        onboarding.ensure_onboarding_for_user(self.user)
        with patch(
            "app.views.stripe_service.create_checkout_session",
            return_value="https://stripe.test/session",
        ) as mock_create:
            resp = self.client.post(
                reverse("billing-upgrade"), {"next": reverse("onboarding")}
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/session")
        self.assertTrue(mock_create.called)
        kwargs = mock_create.call_args.kwargs
        self.assertIn("success_url", kwargs)
        self.assertIn("session_id={CHECKOUT_SESSION_ID}", kwargs["success_url"])

    def test_onboarding_checkout_completion_marks_paid(self):
        onboarding_record = onboarding.ensure_onboarding_for_user(self.user)
        with patch(
            "app.views.stripe_service.complete_checkout_session",
            return_value=self.account,
        ) as mock_complete, patch("app.onboarding.kickoff_after_payment") as mock_kickoff:
            mock_kickoff.return_value = None
            resp = self.client.get(
                reverse("onboarding"), {"session_id": "cs_test_checkout"}
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("onboarding"))
        mock_complete.assert_called_once_with("cs_test_checkout")
        onboarding_record.refresh_from_db()
        self.assertEqual(
            onboarding_record.state, models.Onboarding.State.CHECKOUT_PAID
        )
        mock_kickoff.assert_called_once_with(onboarding_record.id)

    def test_manual_menu(self):
        resp = self.client.get(reverse("manual-menu"), HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Manual menu submission")

    def test_manual_menu_creates_menu_version_from_text(self):
        payload = {"menu_text": "Lunch\n- Soup of the Day"}
        resp = self.client.post(
            reverse("manual-menu"), payload, HTTP_HX_REQUEST="true"
        )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(resp["HX-Redirect"], reverse("onboarding"))

        menu_version = models.MenuVersion.objects.get(restaurant=self.restaurant)
        self.assertEqual(menu_version.source_kind, models.MenuVersion.SourceKind.PASTED_TEXT)
        self.assertEqual(menu_version.raw_markdown, "Lunch\n- Soup of the Day")
        self.assertTrue(self.client.session.get("menu_success"))

    def test_manual_menu_requires_content(self):
        resp = self.client.post(reverse("manual-menu"), {}, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, "Paste your menu or upload a PDF", status_code=400)
        self.assertEqual(models.MenuVersion.objects.count(), 0)

    def test_outscraper_webhook_saves_reviews(self):
        self.restaurant.google_place_id = "place-123"
        self.restaurant.save(update_fields=["google_place_id"])
        models.Onboarding.objects.create(
            user=self.user,
            restaurant=self.restaurant,
            state=models.Onboarding.State.CHECKOUT_PAID,
        )

        payload = {
            "data": [
                [
                    {
                        "name": "R",
                        "place_id": "place-123",
                        "rating": 4.7,
                        "reviews_count": 25,
                        "reviews_data": [
                            {"review_text": "Great!", "rating": 5},
                        ],
                    }
                ]
            ]
        }
        token = onboarding.sign_restaurant_token(self.restaurant.id)
        url = reverse("outscraper_webhook", args=[self.restaurant.id, token])
        resp = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertJSONEqual(resp.content, {"status": "ok"})
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.review_count, 25)
        self.assertAlmostEqual(float(self.restaurant.rating), 4.7)
        self.assertEqual(self.restaurant.reviews_json, payload)
        onboarding_record = models.Onboarding.objects.get(restaurant=self.restaurant)
        self.assertEqual(onboarding_record.state, models.Onboarding.State.REVIEWS_DONE)

    @override_settings(OUTSCRAPER_API_KEY="test-key")
    def test_refresh_reviews_calls_outscraper(self):
        self.restaurant.google_place_id = "abc123"
        self.restaurant.save(update_fields=["google_place_id"])

        with patch("app.views.requests.get") as mock_get:
            resp = self.client.post(
                reverse("refresh_reviews", args=[self.restaurant.id])
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("settings"))
        mock_get.assert_called_once()
        kwargs = mock_get.call_args.kwargs
        self.assertIn("params", kwargs)
        self.assertEqual(kwargs["headers"], {"X-API-KEY": "test-key"})
        self.assertEqual(kwargs["params"]["query"], "abc123")
        webhook_url = kwargs["params"]["webhook"]
        self.assertIn(str(self.restaurant.id), webhook_url)
        token = webhook_url.rstrip("/").split("/")[-1]
        self.assertTrue(onboarding.verify_restaurant_token(token, self.restaurant.id))

    def test_dashboard_displays_contextual_ai_component(self):
        self.restaurant.context_json = {
            "category": "Italian",
            "city": "Austin",
            "reviews_tags": ["cozy ambiance"],
        }
        self.restaurant.save(update_fields=["context_json"])

        resp = self.client.get(reverse("dashboard", args=[self.restaurant.id]))
        self.assertContains(resp, "Inspire Me")
        self.assertContains(resp, "Italian chef&#x27;s table")
        self.assertContains(resp, "Creative bias: 50/100")

    def test_dashboard_shows_collaboration_feedback_summary(self):
        concept_run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.CONCEPTS,
            model_name="concept",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        concept = models.Concept.objects.create(
            restaurant=self.restaurant,
            ideation_run=concept_run,
            name="Signature",
            rank_order=1,
        )
        concept.sketch_image_url = "https://example.com/sketch.jpg"
        concept.save(update_fields=["sketch_image_url"])
        models.FavoriteConcept.objects.create(
            user=self.user,
            concept=concept,
            favorited_at=timezone.now(),
        )

        dish_run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.DISHES,
            model_name="dish",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            parent_concept=concept,
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        dish = models.DishIdea.objects.create(
            restaurant=self.restaurant,
            ideation_run=dish_run,
            parent_concept=concept,
            title="Seared Scallops",
            description="Rich",
            ingredient_names=[],
            category_tags=[],
        )
        models.FavoriteDish.objects.create(
            user=self.user,
            dish=dish,
            favorited_at=timezone.now(),
        )

        menu = models.MenuCollection.objects.create(
            restaurant=self.restaurant,
            created_by_user=self.user,
            name="Chef's Table",
        )
        models.MenuItem.objects.create(menu=menu, dish=dish, position=1)
        link = models.CollaborationLink.objects.create(
            menu=menu,
            expires_at=timezone.now() + timedelta(days=7),
            is_active=True,
        )
        feedback = models.Feedback.objects.create(
            menu=menu,
            dish=dish,
            link=link,
            type=models.Feedback.Type.COMMENT,
            payload={"comment": "Consider extra citrus"},
            anon_id="abc123",
        )
        models.FeedbackAction.objects.create(feedback=feedback)

        resp = self.client.get(reverse("dashboard", args=[self.restaurant.id]))

        recent_concepts = resp.context["recent_concepts"]
        self.assertTrue(recent_concepts)
        self.assertTrue(recent_concepts[0].is_favorited_for_user)
        self.assertEqual(recent_concepts[0].sketch_image_url, "https://example.com/sketch.jpg")

        recent_dishes = resp.context["recent_dishes"]
        self.assertTrue(recent_dishes)
        self.assertTrue(recent_dishes[0].needs_collab_attention)
        self.assertEqual(recent_dishes[0].pending_feedback_count, 1)

        self.assertEqual(resp.context["pending_collaboration_total"], 1)
        self.assertEqual(resp.context["collaboration_updates_more"], 0)
        updates = resp.context["collaboration_updates"]
        self.assertTrue(updates)
        self.assertEqual(updates[0]["menu_name"], menu.name)

    def test_concepts_page_includes_contextual_ai_input(self):
        self._create_concepts()
        self.restaurant.context_json = {"category": "Latin", "city": "Denver"}
        self.restaurant.save(update_fields=["context_json"])

        resp = self.client.get(reverse("concepts"))
        self.assertContains(resp, "concepts-ai-input")
        self.assertContains(resp, "Inspire Me")
        self.assertContains(resp, "Creative bias: 50/100")

    def test_concepts_and_generation(self):
        self._create_concepts()
        resp = self.client.get(reverse("concepts"))
        self.assertContains(resp, "C0")
        payload = {
            "concepts": [
                {
                    "title": f"New {i}",
                    "subtitle": "Sub",
                    "reasoning": "Because",
                    "tags": ["tag1", "tag2", "tag3"],
                }
                for i in range(9)
            ]
        }
        fake_response = SimpleNamespace(
            output=[SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])]
        )

        with patch("app.views.client") as mock_client:
            mock_client.responses.create.return_value = fake_response
            gen_resp = self.client.post(reverse("concepts-generate"))
        self.assertEqual(gen_resp.status_code, 302)
        self.assertEqual(gen_resp["Location"], reverse("concepts"))

    def test_concept_generation_respects_creativity_slider(self):
        settings = self.restaurant.restaurantsettings
        settings.classic_creative_slider = 80
        settings.save(update_fields=["classic_creative_slider"])

        payload = {
            "concepts": [
                {
                    "title": f"Spice {i}",
                    "subtitle": "Sub",
                    "reasoning": "Because",
                    "tags": ["tag1", "tag2", "tag3"],
                }
                for i in range(9)
            ]
        }
        fake_response = SimpleNamespace(
            output=[SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])]
        )

        with patch("app.views.client") as mock_client:
            mock_client.responses.create.return_value = fake_response
            self.client.post(reverse("concepts-generate"))
            _, call_kwargs = mock_client.responses.create.call_args

        run = (
            models.IdeationRun.objects.filter(type=models.IdeationRun.RunType.CONCEPTS)
            .order_by("-created_at")
            .first()
        )
        self.assertIsNotNone(run)
        self.assertEqual(run.classic_creative, 80)
        self.assertEqual(run.temperature, Decimal("0.74"))
        self.assertEqual(run.context_snapshot["classic_creative_slider"], 80)
        self.assertAlmostEqual(run.context_snapshot["temperature"], 0.74, places=2)
        self.assertAlmostEqual(call_kwargs["temperature"], 0.74, places=2)

    def test_concept_generation_updates_slider_setting(self):
        models.RestaurantSettings.objects.filter(restaurant=self.restaurant).delete()

        payload = {
            "concepts": [
                {
                    "title": f"Idea {i}",
                    "subtitle": "Sub",
                    "reasoning": "Why",
                    "tags": ["t1", "t2", "t3"],
                }
                for i in range(9)
            ]
        }
        fake_response = SimpleNamespace(
            output=[SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])]
        )

        with patch("app.views.client") as mock_client:
            mock_client.responses.create.return_value = fake_response
            response = self.client.post(
                reverse("concepts-generate"),
                {"classic_creative_slider": "72"},
            )

        self.assertEqual(response.status_code, 302)
        settings = models.RestaurantSettings.objects.get(restaurant=self.restaurant)
        self.assertEqual(settings.classic_creative_slider, 72)

    def test_dashboard_generation_hx_redirects_to_concepts(self):
        self._create_concepts()
        current_url = f"/dashboard/{self.restaurant.id}/"
        payload = {
            "concepts": [
                {
                    "title": f"Run {i}",
                    "subtitle": "Sub",
                    "reasoning": "Why",
                    "tags": ["t1", "t2", "t3"],
                }
                for i in range(9)
            ]
        }
        fake_response = SimpleNamespace(
            output=[SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])]
        )

        with patch("app.views.client") as mock_client:
            mock_client.responses.create.return_value = fake_response
            response = self.client.post(
                reverse("concepts-generate"),
                HTTP_HX_REQUEST="true",
                HTTP_HX_CURRENT_URL=current_url,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], reverse("concepts"))

    def test_dish_generation_respects_creativity_slider(self):
        self._create_concepts()
        concept = models.Concept.objects.first()

        settings = self.restaurant.restaurantsettings
        settings.classic_creative_slider = 20
        settings.save(update_fields=["classic_creative_slider"])

        payload = {
            "dishes": [
                {
                    "title": f"Dish {i}",
                    "description": "Desc",
                    "ingredient_overlap": ["Item"],
                    "category_tags": ["Tag"],
                }
                for i in range(9)
            ]
        }
        fake_response = SimpleNamespace(
            output=[SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])]
        )

        with patch("app.views.client") as mock_client:
            mock_client.responses.create.return_value = fake_response
            self.client.post(reverse("dishes-generate", args=[concept.id]))
            _, call_kwargs = mock_client.responses.create.call_args

        run = (
            models.IdeationRun.objects.filter(
                type=models.IdeationRun.RunType.DISHES, parent_concept=concept
            )
            .order_by("-created_at")
            .first()
        )
        self.assertIsNotNone(run)
        self.assertEqual(run.classic_creative, 20)
        self.assertEqual(run.temperature, Decimal("0.26"))
        self.assertEqual(run.context_snapshot["classic_creative_slider"], 20)
        self.assertAlmostEqual(run.context_snapshot["temperature"], 0.26, places=2)
        self.assertAlmostEqual(call_kwargs["temperature"], 0.26, places=2)

    def test_dishes_generate_htmx_redirects(self):
        self._create_concepts()
        concept = models.Concept.objects.first()
        payload = {
            "dishes": [
                {
                    "title": "Dish",
                    "description": "Desc",
                    "ingredient_overlap": ["Item"],
                    "category_tags": ["Tag"],
                }
                for _ in range(9)
            ]
        }
        fake_response = SimpleNamespace(
            output=[SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])]
        )

        with patch("app.views.client") as mock_client:
            mock_client.responses.create.return_value = fake_response
            resp = self.client.post(
                reverse("dishes-generate", args=[concept.id]),
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(resp.status_code, 204)
        self.assertEqual(resp["HX-Redirect"], reverse("dish_detail", args=[concept.id]))

    def test_concept_favorite(self):
        self._create_concepts()
        concept = models.Concept.objects.first()
        resp = self.client.post(reverse("concept-favorite", args=[concept.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            models.FavoriteConcept.objects.filter(user=self.user, concept=concept).exists()
        )

    def test_concept_favorite_htmx_redirects_to_dishes(self):
        self._create_concepts()
        concept = models.Concept.objects.first()

        resp = self.client.post(
            reverse("concept-favorite", args=[concept.id]),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(resp.status_code, 204)
        self.assertEqual(resp["HX-Redirect"], reverse("dish_detail", args=[concept.id]))

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

    def test_dish_detail_includes_menu_context(self):
        self._create_concepts()
        concept = models.Concept.objects.first()
        self._create_dishes(concept)
        menu = models.MenuCollection.objects.create(
            restaurant=self.restaurant,
            created_by_user=self.user,
            name="Brunch",
        )

        resp = self.client.get(reverse("dish_detail", args=[concept.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("menu_options", resp.context)
        self.assertIn("menu_move_url", resp.context)
        self.assertEqual(
            resp.context["menu_options"],
            [{"id": str(menu.id), "name": menu.name}],
        )
        self.assertEqual(resp.context["menu_move_url"], reverse("menu-item-move"))
        self.assertEqual(resp.context["menus_workspace_url"], reverse("menus"))
        self.assertEqual(
            resp.context["dishes_generate_url"],
            reverse("dishes-generate", args=[concept.id]),
        )

    def test_dish_detail_empty_menu_renders_workspace_cta(self):
        self._create_concepts()
        concept = models.Concept.objects.first()
        self._create_dishes(concept)

        resp = self.client.get(reverse("dish_detail", args=[concept.id]))
        self.assertContains(resp, "Open menus workspace")
        self.assertContains(resp, f'href="{reverse("menus")}"')
        self.assertContains(resp, "data-menu-empty-cta")

    def test_dish_detail_excludes_deleted_dishes(self):
        self._create_concepts()
        concept = models.Concept.objects.first()
        self._create_dishes(concept)
        dish = models.DishIdea.objects.first()
        dish.is_deleted = True
        dish.save(update_fields=["is_deleted"])

        resp = self.client.get(reverse("dish_detail", args=[concept.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, dish.title)

    def test_dish_detail_displays_concept_metadata(self):
        concept_run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.CONCEPTS,
            model_name="m",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        concept = models.Concept.objects.create(
            restaurant=self.restaurant,
            ideation_run=concept_run,
            name="Coastal Brunch",
            subtitle="Bright citrus and seafood pairings",
            reasoning="Highlight local catch with citrus.\nGreat for summer afternoons.",
            tags=["Seasonal", "Coastal"],
            rank_order=0,
        )
        self._create_dishes(concept)

        resp = self.client.get(reverse("dish_detail", args=[concept.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Coastal Brunch")
        self.assertContains(resp, "Bright citrus and seafood pairings")
        self.assertContains(resp, "Seasonal")
        self.assertContains(resp, "Highlight local catch with citrus.")

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
        dish.refresh_from_db()
        self.assertTrue(dish.is_deleted)
        self.assertFalse(
            models.Enhancement.objects.filter(dish_id=dish.id).exists()
        )
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

        concept_run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.CONCEPTS,
            model_name="m",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        concept = models.Concept.objects.create(
            restaurant=self.restaurant,
            ideation_run=concept_run,
            name="Fav",
            rank_order=1,
        )
        models.FavoriteConcept.objects.create(
            user=self.user, concept=concept, favorited_at=timezone.now()
        )
        concept.sketch_image_url = "https://example.com/favorite.png"
        concept.save(update_fields=["sketch_image_url"])

        dish_run = models.IdeationRun.objects.create(
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
        dish = models.DishIdea.objects.create(
            restaurant=self.restaurant,
            ideation_run=dish_run,
            parent_concept=concept,
            title="Favorite Dish",
            description="Desc",
            ingredient_names=[],
            category_tags=[],
        )
        models.FavoriteDish.objects.create(
            user=self.user, dish=dish, favorited_at=timezone.now()
        )
        asset = models.Asset.objects.create(
            kind=models.Asset.Kind.IMAGE,
            storage_key="enhanced/fav",
            public_url="https://example.com/enhanced.jpg",
        )
        models.Enhancement.objects.create(
            dish=dish,
            status=models.Enhancement.Status.SUCCEEDED,
            image_asset=asset,
            suggested_price_cents=2500,
            currency="USD",
            model_name="enhanced",
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        menu = models.MenuCollection.objects.create(
            restaurant=self.restaurant,
            created_by_user=self.user,
            name="Dinner",
        )
        models.MenuItem.objects.create(menu=menu, dish=dish, position=1)

        resp = self.client.get(reverse("favorites"))
        self.assertEqual(resp.status_code, 200)
        favorite_concepts = resp.context["favorite_concepts"]
        self.assertTrue(favorite_concepts)
        self.assertTrue(
            getattr(favorite_concepts[0].concept, "is_favorited_for_user", False)
        )
        menus = resp.context["menus"]
        self.assertEqual(len(menus), 1)
        self.assertEqual(menus[0].name, "Dinner")
        self.assertEqual(len(menus[0].menu_items), 1)
        self.assertEqual(str(menus[0].menu_items[0].dish.id), str(dish.id))
        self.assertEqual(resp.context["uncategorized_favorites"], [])
        favorite_dishes = resp.context["favorite_dishes"]
        self.assertTrue(favorite_dishes)
        enhanced = favorite_dishes[0].dish
        self.assertTrue(getattr(enhanced, "is_enhanced", False))
        self.assertEqual(enhanced.enhancement_image_url, asset.public_url)
        self.assertEqual(enhanced.enhancement_price_display, "$25.00")
        self.assertEqual(
            resp.context["menu_options"],
            [{"id": str(menu.id), "name": menu.name}],
        )
        self.assertEqual(resp.context["menu_move_url"], reverse("menu-item-move"))

        rem_resp = self.client.post(
            reverse("favorite-remove", args=["concept", concept.id]),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(rem_resp.status_code, 200)
        self.assertEqual(rem_resp.content.decode(), "")
        concept.refresh_from_db()
        self.assertIsNone(concept.sketch_image_url)

    def test_menus_page_lists_menus(self):
        concept_run = models.IdeationRun.objects.create(
            restaurant=self.restaurant,
            initiated_by_user=self.user,
            type=models.IdeationRun.RunType.CONCEPTS,
            model_name="m",
            temperature=0,
            classic_creative=50,
            context_snapshot={},
            status=models.IdeationRun.Status.SUCCEEDED,
        )
        concept = models.Concept.objects.create(
            restaurant=self.restaurant,
            ideation_run=concept_run,
            name="Seasonal",
            rank_order=1,
        )
        dish_run = models.IdeationRun.objects.create(
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
        dish = models.DishIdea.objects.create(
            restaurant=self.restaurant,
            ideation_run=dish_run,
            parent_concept=concept,
            title="Seasonal Dish",
            description="Desc",
            ingredient_names=[],
            category_tags=[],
        )
        menu = models.MenuCollection.objects.create(
            restaurant=self.restaurant,
            created_by_user=self.user,
            name="Tasting",
        )
        models.MenuItem.objects.create(menu=menu, dish=dish, position=1)

        resp = self.client.get(reverse("menus"))
        self.assertEqual(resp.status_code, 200)
        menus = resp.context["menus"]
        self.assertEqual(len(menus), 1)
        self.assertEqual(menus[0].name, menu.name)
        self.assertEqual(len(menus[0].menu_items), 1)
        self.assertEqual(
            resp.context["menu_options"],
            [{"id": str(menu.id), "name": menu.name}],
        )
        self.assertEqual(resp.context["menu_move_url"], reverse("menu-item-move"))

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
        self.assertTrue(
            models.MenuItem.objects.filter(menu_id=collection_id, dish=dish).exists()
        )

        second_resp = self.client.post(
            reverse("menu-collection-create"), {"name": "Second"}
        )
        self.assertEqual(second_resp.status_code, 200)
        second_id = second_resp.json()["id"]

        move_resp = self.client.post(
            reverse("menu-item-move"),
            data=json.dumps(
                {
                    "dish_id": str(dish.id),
                    "source_menu_id": collection_id,
                    "target_menu_id": second_id,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(move_resp.status_code, 200)
        self.assertTrue(
            models.MenuItem.objects.filter(menu_id=second_id, dish=dish).exists()
        )
        self.assertFalse(
            models.MenuItem.objects.filter(menu_id=collection_id, dish=dish).exists()
        )

        remove_resp = self.client.post(
            reverse("menu-item-move"),
            data=json.dumps(
                {
                    "dish_id": str(dish.id),
                    "source_menu_id": second_id,
                    "target_menu_id": "",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(remove_resp.status_code, 200)
        self.assertFalse(
            models.MenuItem.objects.filter(menu_id=second_id, dish=dish).exists()
        )

        rename_resp = self.client.post(
            reverse("menu-collection-rename", args=[second_id]),
            {"name": "Updated"},
        )
        self.assertEqual(rename_resp.status_code, 200)
        self.assertEqual(rename_resp.json()["name"], "Updated")

        delete_resp = self.client.post(
            reverse("menu-collection-delete", args=[second_id])
        )
        self.assertEqual(delete_resp.status_code, 200)
        self.assertFalse(models.MenuCollection.objects.filter(id=second_id).exists())

    def test_menu_collection_requires_name(self):
        response = self.client.post(reverse("menu-collection-create"), {"name": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "name_required")

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
            {
                "form_type": "urls",
                "menu_urls": "https://example.com/new-menu\nhttps://example.com/archive",
            },
        )
        self.assertEqual(update_resp.status_code, 302)
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.primary_menu_url, "https://example.com/new-menu")
        self.assertEqual(
            self.restaurant.menu_urls,
            ["https://example.com/new-menu", "https://example.com/archive"],
        )

        content_resp = self.client.post(
            reverse("update_restaurant_info"),
            {"form_type": "content", "menu_text": "Updated Menu"},
        )
        self.assertEqual(content_resp.status_code, 302)
        self.restaurant.refresh_from_db()
        latest_version = self.restaurant.active_menu_version
        self.assertIsNotNone(latest_version)
        self.assertEqual(latest_version.raw_markdown, "Updated Menu")
        self.assertEqual(
            latest_version.source_kind, models.MenuVersion.SourceKind.PASTED_TEXT
        )

        self.restaurant.set_menu_urls(["https://example.com/menu"])
        self.restaurant.save(update_fields=["menu_urls", "primary_menu_url"])
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
        self.restaurant.set_menu_urls([])
        self.restaurant.save(update_fields=["menu_urls", "primary_menu_url"])

        response = self.client.get(reverse("dashboard", args=[self.restaurant.id]))
        self.assertContains(response, "show_menu_modal")

    def test_dashboard_does_not_prompt_when_menu_exists(self):
        self.restaurant.set_menu_urls(["https://example.com/menu"])
        self.restaurant.save(update_fields=["menu_urls", "primary_menu_url"])

        response = self.client.get(reverse("dashboard", args=[self.restaurant.id]))
        self.assertNotIn("show_menu_modal", response.content.decode())

    def test_dashboard_context_toggle_updates_preference(self):
        self.restaurant.description = "Tasty story"
        self.restaurant.save(update_fields=["description"])

        toggle_url = reverse("dashboard-context-toggle", args=[self.restaurant.id])
        resp = self.client.post(toggle_url, {"key": "story", "include": "false"})
        self.assertEqual(resp.status_code, 200)

        settings = self.restaurant.restaurantsettings
        settings.refresh_from_db()
        self.assertIn("context_flags", settings.llm_defaults)
        self.assertFalse(settings.llm_defaults["context_flags"]["story"])

    def test_context_checklist_reflects_data_sources(self):
        settings = self.restaurant.restaurantsettings
        settings.llm_defaults = {}
        settings.save(update_fields=["llm_defaults"])

        self.restaurant.active_menu_version = None
        self.restaurant.about_json = None
        self.restaurant.description = ""
        self.restaurant.review_count = None
        self.restaurant.rating = None
        self.restaurant.context_json = {}
        self.restaurant.set_menu_urls([])
        self.restaurant.save(
            update_fields=[
                "active_menu_version",
                "about_json",
                "description",
                "review_count",
                "rating",
                "context_json",
                "menu_urls",
                "primary_menu_url",
            ]
        )

        models.MenuVersion.objects.filter(restaurant=self.restaurant).delete()
        models.Ingredient.objects.filter(restaurant=self.restaurant).delete()

        items = views.build_context_items(self.restaurant, settings)
        presence = {item["key"]: item["present"] for item in items}
        self.assertEqual(
            presence,
            {
                "menu": False,
                "menu_content": False,
                "services": False,
                "story": False,
                "reviews": False,
                "ingredients": False,
            },
        )

        menu_version = models.MenuVersion.objects.create(
            restaurant=self.restaurant,
            source_url="https://example.com/menu",
            source_kind=models.MenuVersion.SourceKind.URL_SCRAPE,
            raw_markdown="Menu text",
            status=models.MenuVersion.Status.SUCCEEDED,
        )
        self.restaurant.active_menu_version = menu_version
        self.restaurant.set_menu_urls(["https://example.com/menu"])
        self.restaurant.about_json = {"Service options": {"Dine-in": True}}
        self.restaurant.description = "House story"
        self.restaurant.review_count = 5
        self.restaurant.context_json = {"reviews_tags": ["friendly staff"]}
        self.restaurant.save(
            update_fields=[
                "active_menu_version",
                "menu_urls",
                "primary_menu_url",
                "about_json",
                "description",
                "review_count",
                "context_json",
            ]
        )
        models.Ingredient.objects.create(restaurant=self.restaurant, name="Salt")

        settings.llm_defaults = {}
        settings.save(update_fields=["llm_defaults"])

        refreshed_items = views.build_context_items(self.restaurant, settings)
        refreshed_presence = {item["key"]: item["present"] for item in refreshed_items}
        self.assertEqual(
            refreshed_presence,
            {
                "menu": True,
                "menu_content": True,
                "services": True,
                "story": True,
                "reviews": True,
                "ingredients": True,
            },
        )

    def test_logout_via_get_redirects(self):
        response = self.client.get(reverse("logout"))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_rescrape_menu_requires_url(self):
        self.restaurant.set_menu_urls([])
        self.restaurant.save(update_fields=["menu_urls", "primary_menu_url"])
        with patch("app.views.scrape_menu.delay") as mock_delay:
            resp = self.client.post(
                reverse("settings-rescrape-menu", args=[self.restaurant.id])
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "missing_menu_url")
        self.assertFalse(models.MenuVersion.objects.exists())
        mock_delay.assert_not_called()

    @patch("app.views.parse_pdf_menu.delay")
    def test_settings_pdf_upload_queues_processing(self, mock_parse):
        pdf_file = SimpleUploadedFile(
            "menu.pdf", b"%PDF-1.4 test", content_type="application/pdf"
        )

        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                reverse("update_restaurant_info"),
                {"form_type": "content", "menu_pdf": pdf_file},
            )

        self.assertEqual(resp.status_code, 302)
        mv = models.MenuVersion.objects.latest("created_at")
        self.assertEqual(mv.status, models.MenuVersion.Status.QUEUED)
        self.assertEqual(mv.source_kind, models.MenuVersion.SourceKind.IMAGE_OCR)
        mock_parse.assert_called_once()

    @patch("app.stripe.stripe.Subscription.modify")
    @patch("app.stripe.stripe.checkout.Session.create")
    def test_billing_views(self, mock_checkout, mock_modify):
        mock_checkout.return_value = SimpleNamespace(url="https://stripe.test/session")

        resp = self.client.get(reverse("billing"))
        self.assertEqual(resp.status_code, 200)

        up = self.client.post(
            reverse("billing-upgrade"), {"next": reverse("billing")}
        )
        self.assertEqual(up.status_code, 302)
        self.assertEqual(up["Location"], "https://stripe.test/session")
        mock_checkout.assert_called_once()

        cancel = self.client.post(reverse("billing-cancel"))
        self.assertEqual(cancel.status_code, 302)

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
