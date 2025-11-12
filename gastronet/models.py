from django.db import models
from django.contrib.gis.db import models as gis_models
from pgvector.django import VectorField
from django.utils import timezone


class Restaurant(models.Model):
    place_id = models.CharField(max_length=400, unique=True, db_index=True)

    # --- Core identity ---
    name = models.CharField(max_length=255, db_index=True)
    address = models.TextField(null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    website = models.URLField(max_length=2000, null=True, blank=True)
    phone = models.CharField(max_length=50, null=True, blank=True)
    menu_url = models.URLField(max_length=2000, null=True, blank=True)
    url_checked_at = models.DateTimeField(null=True, blank=True)
    url_source = models.CharField(max_length=100, null=True, blank=True)  # "bing" | "heuristic" | "llm"
    description = models.TextField(null=True, blank=True)

    # --- Classification ---
    category = models.CharField(max_length=500, null=True, blank=True)
    cuisine = models.CharField(max_length=100, null=True, blank=True)

    # --- Metrics ---
    rating = models.FloatField(null=True, blank=True)
    review_count = models.IntegerField(default=0)
    sentiment_score = models.FloatField(null=True, blank=True)

    # --- Geo + Embeddings ---
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    location = gis_models.PointField(geography=True, null=True, blank=True)
    embedding = VectorField(dimensions=1536, null=True, blank=True)

    # --- AI summaries ---
    summary = models.TextField(null=True, blank=True)
    keywords = models.JSONField(null=True, blank=True)

    # --- Pipeline freshness / lifecycle ---
    source = models.CharField(max_length=50, default="outscraper")
    last_review_date = models.DateTimeField(null=True, blank=True)
    avg_review_gap_days = models.FloatField(null=True, blank=True)
    next_fetch_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    last_updated = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    # --- Additional structured data ---
    hours = models.JSONField(null=True, blank=True)              # working_hours
    about = models.JSONField(null=True, blank=True)              # attributes like "Service options"
    price_range = models.CharField(max_length=20, null=True, blank=True)
    logo_url = models.URLField(max_length=2000, null=True, blank=True)
    photo_url = models.URLField(max_length=2000, null=True, blank=True)
    street_view = models.URLField(max_length=2000, null=True, blank=True)
    location_link = models.URLField(max_length=2000, null=True, blank=True)
    booking_appointment_link = models.URLField(max_length=2000, null=True, blank=True)
    owner_link = models.URLField(max_length=2000, null=True, blank=True)
    reviews_url = models.URLField(max_length=2000, null=True, blank=True)
    reservation_links = models.JSONField(null=True, blank=True)
    order_links = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["city", "category"]),
            models.Index(fields=["city", "active"]),
            models.Index(fields=["next_fetch_at"]),
        ]
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def short_summary(self):
        if self.summary:
            return self.summary[:140] + ("..." if len(self.summary) > 140 else "")
        return ""

class Review(models.Model):
    restaurant = models.ForeignKey(
        Restaurant, on_delete=models.CASCADE, related_name="reviews"
    )
    source = models.CharField(max_length=50)
    review_id = models.CharField(max_length=200, db_index=True)
    rating = models.FloatField(null=True, blank=True)
    text = models.TextField()
    created_at = models.DateTimeField()
    scraped_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("restaurant", "source", "review_id")]
        indexes = [models.Index(fields=["restaurant", "created_at"])]

    def __str__(self):
        return f"{self.restaurant.name} [{self.source}] {self.review_id}"


class CrawlLog(models.Model):
    """Single source of truth for pipeline health and cost awareness."""

    task = models.CharField(max_length=100)
    scope = models.CharField(max_length=200, null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    success_count = models.IntegerField(default=0)
    skip_count = models.IntegerField(default=0)
    error_count = models.IntegerField(default=0)
    api_calls = models.IntegerField(default=0)
    est_cost_usd = models.FloatField(default=0.0)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.task} ({self.started_at.isoformat()})"

class UrlDiscovery(models.Model):
    query = models.CharField(max_length=255, unique=True)
    result_url = models.URLField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    hit_count = models.IntegerField(default=1)


class MenuAttempt(models.Model):
    restaurant = models.ForeignKey("Restaurant", on_delete=models.CASCADE, related_name="menu_attempts")
    tried_url = models.URLField(null=True, blank=True)
    source = models.CharField(max_length=50, null=True, blank=True)  # "heuristic"|"follow_link"|"llm"
    found = models.BooleanField(default=False)
    parsed = models.BooleanField(default=False)
    status = models.CharField(max_length=200, null=True, blank=True)  # short note or error
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    def finish(self, found=False, parsed=False, status=None):
        self.found = found
        self.parsed = parsed
        self.status = status
        self.finished_at = timezone.now()
        self.save(update_fields=["found","parsed","status","finished_at"])

    def __str__(self):
        return f"{self.restaurant.name} @ {self.tried_url} ({self.created_at:%Y-%m-%d})"

class MenuSnapshot(models.Model):
    restaurant = models.ForeignKey(
        "Restaurant",
        on_delete=models.CASCADE,
        related_name="menu_snapshots",
    )
    fetched_at = models.DateTimeField(auto_now_add=True)
    source_url = models.URLField()
    text = models.TextField()               # raw plain-text
    hash = models.CharField(max_length=64)  # SHA256 for change detection
    parsed_json = models.JSONField(null=True, blank=True)  # optional LLM output
    summary = models.TextField(null=True, blank=True)   
    render_method = models.CharField(max_length=50, null=True, blank=True)  # "plain"|"playwright"|"scraperapi"

# gastronet/models.py
class MenuItem(models.Model):
    restaurant = models.ForeignKey("gastronet.Restaurant", on_delete=models.CASCADE, related_name="menu_items")
    source_url = models.URLField(max_length=500)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    section = models.CharField(max_length=255, blank=True)
    dietary_tags = models.JSONField(default=list, blank=True)
    currency = models.CharField(max_length=8, default="USD")
    scraped_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("restaurant", "source_url", "name")