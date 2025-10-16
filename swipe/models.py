from django.conf import settings
from django.db import models
from django.utils import timezone
from app.models import Restaurant


class SeenItem(models.Model):
    class ItemType(models.TextChoices):
        CONCEPT = "concept", "Concept"
        DISH = "dish", "Dish"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    item_type = models.CharField(max_length=20, choices=ItemType.choices)
    item_id = models.PositiveIntegerField()
    seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("user", "item_type", "item_id")
        indexes = [
            models.Index(fields=["user", "item_type"]),
            models.Index(fields=["item_type", "item_id"]),
        ]

# Concept and Dish are lightweight; can be generated, cached, or persisted later.
CONCEPT_PLACEHOLDER_URL = "https://placehold.co/1200x800?text=Concept"
DISH_PLACEHOLDER_URL = "https://placehold.co/800x600?text=Dish"


class Concept(models.Model):
    # NOTE: In production you'll likely want a Restaurant FK. Omitted for skeleton.
    restaurant = models.ForeignKey(Restaurant,on_delete=models.CASCADE,related_name="concepts")
    name = models.CharField(max_length=200)
    subtitle = models.CharField(max_length=240, blank=True)
    sketch_url = models.URLField(blank=True)
    sketch_prompt = models.CharField(max_length=1240, blank=True)
    meta_ingredients = models.JSONField(default=list)  # list[str]
    meta_reasoning = models.TextField(blank=True)
    is_favorite = models.BooleanField(default=False)
    is_seen = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.name

    @property
    def display_sketch_url(self) -> str:
        url = (self.sketch_url or "").strip()
        if not url or url.lower() in {"none", "null"}:
            return CONCEPT_PLACEHOLDER_URL
        return url


class Dish(models.Model):
    concept = models.ForeignKey(Concept, on_delete=models.CASCADE, related_name="dishes")
    name = models.CharField(max_length=200)
    image_url = models.URLField(blank=True)
    price = models.CharField(max_length=32, blank=True)  # keep string for "$18" format
    ingredients = models.JSONField(default=list)  # list[str]
    reasoning = models.TextField(blank=True)
    is_favorite = models.BooleanField(default=False)
    is_seen = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} ({self.concept.name})"

    @property
    def display_image_url(self) -> str:
        url = (self.image_url or "").strip()
        if not url or url.lower() in {"none", "null"}:
            return DISH_PLACEHOLDER_URL
        return url


class Favorite(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    concept = models.ForeignKey(Concept, null=True, blank=True, on_delete=models.CASCADE)
    dish = models.ForeignKey(Dish, null=True, blank=True, on_delete=models.CASCADE)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = (("user", "concept"), ("user", "dish"))
