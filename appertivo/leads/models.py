"""Database models for the leads app."""
from __future__ import annotations

from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class Lead(models.Model):
    """A potential restaurant lead discovered via external data sources."""

    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    slug = models.SlugField(unique=True)
    landing_url = models.URLField(blank=True, null=True)
    json_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

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
