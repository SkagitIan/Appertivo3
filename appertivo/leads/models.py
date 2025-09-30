"""Database models for the leads app."""
from __future__ import annotations

from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class LeadRun(models.Model):
    """A batch of leads fetched together from Outscraper."""

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        FETCHING = "fetching", "Fetching leads"
        PREPARING = "preparing", "Preparing demos"
        READY = "ready", "Ready for review"
        COMPLETED = "completed", "Completed"

    city = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CREATED,
    )
    outscraper_job_id = models.CharField(max_length=100, blank=True, null=True, unique=True)
    expected_leads = models.PositiveIntegerField(default=10)
    total_leads = models.PositiveIntegerField(default=0)
    processed_leads = models.PositiveIntegerField(default=0)
    selected_leads = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        city_label = self.city or "Any city"
        return f"Run {self.pk} – {city_label}"

    @property
    def progress_percentage(self) -> int:
        """Return an integer percentage of processed leads within the run."""

        target = self.expected_leads or self.total_leads or 0
        if not target:
            return 0
        progress = int((self.processed_leads / target) * 100)
        return min(100, max(0, progress))


class Lead(models.Model):
    """A potential restaurant lead discovered via external data sources."""

    run = models.ForeignKey(
        LeadRun,
        on_delete=models.CASCADE,
        related_name="leads",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    slug = models.SlugField(unique=True)
    landing_url = models.URLField(blank=True, null=True)
    json_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    shortlisted = models.BooleanField(default=False)
    emailed = models.BooleanField(default=False)
    opened = models.BooleanField(default=False)
    followed_up = models.BooleanField(default=False)
    converted = models.BooleanField(default=False)
    restaurant = models.ForeignKey(
        "app.Restaurant",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leads",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.city or 'Unknown city'})"

    def save(self, *args, **kwargs) -> None:  # type: ignore[override]
        """Ensure a slug and landing URL are generated when saving."""

        if not self.slug:
            base_slug = slugify(self.name) or "lead"
            slug_candidate = base_slug
            counter = 1
            while Lead.objects.exclude(pk=self.pk).filter(slug=slug_candidate).exists():
                counter += 1
                slug_candidate = f"{base_slug}-{counter}"
            self.slug = slug_candidate
        super().save(*args, **kwargs)
        if not self.landing_url:
            landing_path = reverse("lead-landing", args=[self.slug])
            self.landing_url = f"https://appertivo.com{landing_path}"
            super().save(update_fields=["landing_url"])


class Concept(models.Model):
    """A demo concept generated for a lead."""

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="concepts")
    name = models.CharField(max_length=255)
    rank_order = models.SmallIntegerField()
    enhanced = models.BooleanField(default=False)

    class Meta:
        ordering = ["rank_order"]

    def __str__(self) -> str:
        return f"{self.name} (Lead: {self.lead_id})"


class DishIdea(models.Model):
    """A dish idea associated with a lead and optionally a concept."""

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="dishes")
    concept = models.ForeignKey(
        Concept,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="dishes",
    )
    title = models.CharField(max_length=255)
    favorited = models.BooleanField(default=False)
    image_url = models.URLField(blank=True, null=True)

    class Meta:
        ordering = ["-favorited", "id"]

    def __str__(self) -> str:
        return self.title
