"""Database models for the app."""

import uuid
from typing import Iterable, List

from django.conf import settings
from django.db import models
from django.db.models import Q


class TimestampedModel(models.Model):
    """Abstract base model with UUID id and created_at."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


class Account(TimestampedModel):
    """Organization account."""

    name = models.TextField(null=True, blank=True)


class UserProfile(TimestampedModel):
    """Stores per-user settings not in auth_user."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    timezone = models.TextField(default="America/Los_Angeles")


class Membership(TimestampedModel):
    """Links users to accounts with roles."""

    class Role(models.TextChoices):
        OWNER = "owner"
        ADMIN = "admin"
        MEMBER = "member"

    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.TextField(choices=Role.choices, default=Role.MEMBER)

    class Meta:
        unique_together = ("account", "user")


class Restaurant(TimestampedModel):
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    name = models.TextField()
    location_text = models.TextField()
    primary_menu_url = models.TextField(null=True, blank=True)
    menu_urls = models.JSONField(default=list, blank=True)

    # Outscraper context fields
    phone = models.TextField(null=True, blank=True)
    website = models.TextField(null=True, blank=True)
    google_place_id = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    rating = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    review_count = models.IntegerField(null=True, blank=True)
    hours_json = models.JSONField(null=True, blank=True)     # working_hours
    about_json = models.JSONField(null=True, blank=True)     # amenities, offerings, etc.
    context_json = models.JSONField(null=True, blank=True)   # full Outscraper snapshot

    active_menu_version = models.ForeignKey(
        "MenuVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        indexes = [models.Index(fields=["account", "name"])]

    def __str__(self):
        return self.name

    def set_menu_urls(self, urls: Iterable[str]) -> None:
        """Store a unique, ordered list of menu URLs."""

        cleaned: List[str] = []
        for url in urls:
            normalized = (url or "").strip()
            if not normalized:
                continue
            if normalized not in cleaned:
                cleaned.append(normalized)

        self.menu_urls = cleaned
        self.primary_menu_url = cleaned[0] if cleaned else None

    def add_menu_url(self, url: str) -> None:
        """Append a URL to the stored list if missing."""

        current = list(self.menu_urls or [])
        current.insert(0, url)
        self.set_menu_urls(current)

class OutscraperPayload(TimestampedModel):
    """Tracks Outscraper requests for a restaurant."""
    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    status = models.TextField(choices=Status.choices)
    request_params = models.JSONField()
    response_json = models.JSONField(null=True, blank=True)
    discovered_menu_url = models.TextField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["restaurant", "-created_at"])]


class MenuVersion(TimestampedModel):
    """Version of a restaurant's menu."""
    class SourceKind(models.TextChoices):
        URL_SCRAPE = "url_scrape"
        PASTED_TEXT = "pasted_text"
        IMAGE_OCR = "image_ocr"

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    source_url = models.TextField(null=True, blank=True)
    source_kind = models.TextField(choices=SourceKind.choices)
    raw_markdown = models.TextField()
    parsed_at = models.DateTimeField(null=True, blank=True)
    status = models.TextField(choices=Status.choices)
    error_message = models.TextField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["restaurant", "-created_at"])]


class Ingredient(TimestampedModel):
    """Ingredient identified for a restaurant."""
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    name = models.TextField()
    canonical_name = models.TextField(null=True, blank=True)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, default=1)
    first_seen_menu_version = models.ForeignKey(
        MenuVersion, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["restaurant", "name"], name="unique_restaurant_ingredient"
            )
        ]
        indexes = [models.Index(fields=["name"])]


class IdeationRun(TimestampedModel):
    """LLM run that generates concepts or dishes."""
    class RunType(models.TextChoices):
        CONCEPTS = "concepts"
        DISHES = "dishes"

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    initiated_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    type = models.TextField(choices=RunType.choices)
    model_name = models.TextField()
    temperature = models.DecimalField(max_digits=3, decimal_places=2)
    classic_creative = models.SmallIntegerField()
    context_snapshot = models.JSONField()
    parent_concept = models.ForeignKey(
        "Concept", null=True, blank=True, on_delete=models.CASCADE
    )
    status = models.TextField(choices=Status.choices)
    cost_cents = models.IntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["restaurant", "type", "-created_at"])]


class Concept(TimestampedModel):
    """Concept produced by an ideation run."""
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    ideation_run = models.ForeignKey(IdeationRun, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    subtitle = models.CharField(max_length=200)
    rank_order = models.SmallIntegerField()

    class Meta:
        indexes = [models.Index(fields=["restaurant", "-created_at"])]


class DishIdea(TimestampedModel):
    """Dish idea under a concept."""
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    ideation_run = models.ForeignKey(IdeationRun, on_delete=models.CASCADE)
    parent_concept = models.ForeignKey(Concept, on_delete=models.CASCADE)
    parent_dish = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE
    )
    title = models.TextField()
    description = models.TextField()
    ingredient_names = models.JSONField(default=list)
    category_tags = models.JSONField(default=list)


class DishIdeaIngredient(TimestampedModel):
    """Links dish ideas to ingredients."""
    class Source(models.TextChoices):
        OVERLAP = "overlap"
        INFERRED = "inferred"

    dish = models.ForeignKey(DishIdea, on_delete=models.CASCADE)
    ingredient = models.ForeignKey(Ingredient, on_delete=models.CASCADE)
    source = models.TextField(choices=Source.choices)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, default=1)

    class Meta:
        unique_together = ("dish", "ingredient", "source")


class FavoriteConcept(TimestampedModel):
    """User favorite of a concept."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    concept = models.ForeignKey(Concept, on_delete=models.CASCADE)
    favorited_at = models.DateTimeField()

    class Meta:
        unique_together = ("user", "concept")


class FavoriteDish(TimestampedModel):
    """User favorite of a dish."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    dish = models.ForeignKey(DishIdea, on_delete=models.CASCADE)
    favorited_at = models.DateTimeField()

    class Meta:
        unique_together = ("user", "dish")


class Asset(TimestampedModel):
    """Stored asset such as an image."""
    class Kind(models.TextChoices):
        IMAGE = "image"
        PDF = "pdf"
        OTHER = "other"

    kind = models.TextField(choices=Kind.choices)
    storage_key = models.TextField()
    public_url = models.TextField()
    width_px = models.IntegerField(null=True, blank=True)
    height_px = models.IntegerField(null=True, blank=True)
    format = models.TextField(null=True, blank=True)
    blurhash = models.TextField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["kind", "-created_at"])]


class Enhancement(TimestampedModel):
    """Generated enhancement for a dish idea."""
    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    dish = models.ForeignKey(DishIdea, on_delete=models.CASCADE)
    triggered_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    status = models.TextField(choices=Status.choices)
    image_asset = models.ForeignKey(
        Asset, null=True, blank=True, on_delete=models.SET_NULL
    )
    suggested_price_cents = models.IntegerField(null=True, blank=True)
    currency = models.TextField(default="USD")
    pricing_notes = models.TextField(null=True, blank=True)
    style_preset = models.TextField(null=True, blank=True)
    model_name = models.TextField()
    cost_cents = models.IntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)


class MenuCollection(TimestampedModel):
    """Collection of favorite dishes."""
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    created_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    name = models.TextField()
    description = models.TextField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["restaurant", "name"], name="unique_menu_collection"
            )
        ]


class MenuItem(TimestampedModel):
    """Dish included in a menu collection."""
    menu = models.ForeignKey(MenuCollection, on_delete=models.CASCADE)
    dish = models.ForeignKey(DishIdea, on_delete=models.CASCADE)
    enhancement = models.ForeignKey(
        Enhancement, null=True, blank=True, on_delete=models.SET_NULL
    )
    position = models.SmallIntegerField()
    notes = models.TextField(null=True, blank=True)

    class Meta:
        unique_together = ("menu", "dish")
        indexes = [models.Index(fields=["menu", "position"])]


class RestaurantSettings(TimestampedModel):
    """Configurable settings for a restaurant."""
    restaurant = models.OneToOneField(Restaurant, on_delete=models.CASCADE)
    classic_creative_slider = models.SmallIntegerField(default=50)
    default_currency = models.TextField(default="USD")
    llm_defaults = models.JSONField(default=dict)
    notifications_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


class NotificationPref(TimestampedModel):
    """Per-user notification preferences."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    on_background_complete_email = models.BooleanField(default=True)
    on_new_menu_version_email = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class Notification(TimestampedModel):
    """Notification sent to a user."""
    class Type(models.TextChoices):
        JOB_COMPLETE = "job_complete"
        ENHANCEMENT_READY = "enhancement_ready"
        MENU_RESCRAPED = "menu_rescraped"
        OTHER = "other"

    class Channel(models.TextChoices):
        EMAIL = "email"
        IN_APP = "in_app"

    class Status(models.TextChoices):
        QUEUED = "queued"
        SENT = "sent"
        FAILED = "failed"
        READ = "read"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    type = models.TextField(choices=Type.choices)
    channel = models.TextField(choices=Channel.choices)
    payload = models.JSONField()
    status = models.TextField(choices=Status.choices)
    sent_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "status", "-created_at"])]


class Plan(TimestampedModel):
    """Subscription plan definition."""
    code = models.TextField(unique=True)
    name = models.TextField()
    limits = models.JSONField()
    features = models.JSONField()


class Subscription(TimestampedModel):
    """Subscription for an account."""
    class Provider(models.TextChoices):
        STRIPE = "stripe"
        MANUAL = "manual"

    class Status(models.TextChoices):
        TRIALING = "trialing"
        ACTIVE = "active"
        PAST_DUE = "past_due"
        CANCELED = "canceled"

    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    plan = models.ForeignKey(Plan, on_delete=models.RESTRICT)
    provider = models.TextField(choices=Provider.choices)
    provider_customer_id = models.TextField()
    provider_sub_id = models.TextField()
    status = models.TextField(choices=Status.choices)
    current_period_start = models.DateTimeField()
    current_period_end = models.DateTimeField()
    cancel_at_period_end = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["account"],
                condition=Q(status__in=["trialing", "active", "past_due"]),
                name="unique_live_subscription",
            )
        ]


class EntitlementCounter(TimestampedModel):
    """Monthly usage counters for an account."""
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    period_start = models.DateField()
    concept_runs = models.IntegerField(default=0)
    dish_runs = models.IntegerField(default=0)
    enhancements = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("account", "period_start")


class Job(TimestampedModel):
    """Background job tracking."""
    class Kind(models.TextChoices):
        OUTSCRAPER = "outscraper"
        MENU_SCRAPE = "menu_scrape"
        INGREDIENT_BUILD = "ingredient_build"
        IDEATION = "ideation"
        IMAGE_GENERATE = "image_generate"
        EMAIL_SEND = "email_send"

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    restaurant = models.ForeignKey(
        Restaurant, null=True, blank=True, on_delete=models.SET_NULL
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    kind = models.TextField(choices=Kind.choices)
    ref_table = models.TextField()
    ref_id = models.UUIDField()
    status = models.TextField(choices=Status.choices)
    progress_pct = models.SmallIntegerField()
    error_message = models.TextField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["account", "status", "-created_at"])]


class UiEvent(TimestampedModel):
    """Logged UI interaction event."""
    class EntityType(models.TextChoices):
        CONCEPT = "concept"
        DISH = "dish"
        MENU = "menu"
        ENHANCEMENT = "enhancement"
        OTHER = "other"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    restaurant = models.ForeignKey(
        Restaurant, null=True, blank=True, on_delete=models.SET_NULL
    )
    name = models.TextField()
    entity_type = models.TextField(choices=EntityType.choices)
    entity_id = models.UUIDField(null=True, blank=True)
    extra = models.JSONField(default=dict)

    class Meta:
        indexes = [models.Index(fields=["user", "-created_at"])]


class TagDictionary(TimestampedModel):
    """Dictionary of canonical tags."""
    class Kind(models.TextChoices):
        CATEGORY = "category"

    kind = models.TextField(choices=Kind.choices)
    name = models.TextField()
    slug = models.TextField(unique=True)
