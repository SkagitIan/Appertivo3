from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from app import models


class ModelCreationTests(TestCase):
    def test_model_creation(self):
        user = User.objects.create_user(
            username="u1", password="pw", email="u1@example.com"
        )
        models.UserProfile.objects.create(user=user)

        account = models.Account.objects.create(name="Acc")
        models.Membership.objects.create(account=account, user=user)

        restaurant = models.Restaurant.objects.create(
            account=account, name="R", location_text="City, State"
        )

        mv = models.MenuVersion.objects.create(
            restaurant=restaurant,
            source_kind=models.MenuVersion.SourceKind.URL_SCRAPE,
            raw_markdown="",
            status=models.MenuVersion.Status.QUEUED,
        )

        models.OutscraperPayload.objects.create(
            restaurant=restaurant,
            status=models.OutscraperPayload.Status.QUEUED,
            request_params={},
        )

        ingredient = models.Ingredient.objects.create(
            restaurant=restaurant, name="salt", first_seen_menu_version=mv
        )

        run = models.IdeationRun.objects.create(
            restaurant=restaurant,
            initiated_by_user=user,
            type=models.IdeationRun.RunType.CONCEPTS,
            model_name="gpt",
            temperature=0.5,
            classic_creative=50,
            context_snapshot={},
            status=models.IdeationRun.Status.QUEUED,
        )

        concept = models.Concept.objects.create(
            restaurant=restaurant, ideation_run=run, name="Concept", rank_order=1
        )

        dish_run = models.IdeationRun.objects.create(
            restaurant=restaurant,
            initiated_by_user=user,
            type=models.IdeationRun.RunType.DISHES,
            model_name="gpt",
            temperature=0.5,
            classic_creative=50,
            context_snapshot={},
            parent_concept=concept,
            status=models.IdeationRun.Status.QUEUED,
        )

        dish = models.DishIdea.objects.create(
            restaurant=restaurant,
            ideation_run=dish_run,
            parent_concept=concept,
            title="Dish",
            description="desc",
            ingredient_names=["salt"],
            category_tags=["tag"],
        )

        models.DishIdeaIngredient.objects.create(
            dish=dish,
            ingredient=ingredient,
            source=models.DishIdeaIngredient.Source.OVERLAP,
        )

        models.FavoriteConcept.objects.create(
            user=user, concept=concept, favorited_at=timezone.now()
        )
        models.FavoriteDish.objects.create(
            user=user, dish=dish, favorited_at=timezone.now()
        )

        asset = models.Asset.objects.create(
            kind=models.Asset.Kind.IMAGE, storage_key="k", public_url="url"
        )
        enhancement = models.Enhancement.objects.create(
            dish=dish,
            triggered_by_user=user,
            status=models.Enhancement.Status.QUEUED,
            image_asset=asset,
            model_name="m",
        )

        collection = models.MenuCollection.objects.create(
            restaurant=restaurant, created_by_user=user, name="Menu"
        )
        models.MenuItem.objects.create(
            menu=collection, dish=dish, enhancement=enhancement, position=1
        )

        models.RestaurantSettings.objects.create(restaurant=restaurant)
        models.NotificationPref.objects.create(user=user)
        models.Notification.objects.create(
            user=user,
            type=models.Notification.Type.OTHER,
            channel=models.Notification.Channel.EMAIL,
            payload={},
            status=models.Notification.Status.QUEUED,
        )

        plan = models.Plan.objects.create(code="free", name="Free", limits={}, features={})
        models.Subscription.objects.create(
            account=account,
            plan=plan,
            provider=models.Subscription.Provider.STRIPE,
            provider_customer_id="c",
            provider_sub_id="s",
            status=models.Subscription.Status.ACTIVE,
            current_period_start=timezone.now(),
            current_period_end=timezone.now(),
        )

        models.EntitlementCounter.objects.create(
            account=account, period_start=timezone.now().date()
        )

        models.Job.objects.create(
            account=account,
            restaurant=restaurant,
            user=user,
            kind=models.Job.Kind.IDEATION,
            ref_table="ideation_run",
            ref_id=run.id,
            status=models.Job.Status.QUEUED,
            progress_pct=0,
        )

        models.UiEvent.objects.create(
            user=user,
            restaurant=restaurant,
            name="evt",
            entity_type=models.UiEvent.EntityType.OTHER,
        )

        models.TagDictionary.objects.create(
            kind=models.TagDictionary.Kind.CATEGORY, name="cat", slug="cat"
        )
