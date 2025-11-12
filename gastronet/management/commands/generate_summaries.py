# gastronet/management/commands/generate_summaries.py
import logging
import os

import requests
from django.core.management.base import BaseCommand
from django.db import models
from dotenv import load_dotenv
from openai import OpenAI

from gastronet.models import Restaurant

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
logger = logging.getLogger(__name__)

OUTSCRAPER_API_KEY = os.getenv("OUTSCRAPER_API_KEY", "")
OUTSCRAPER_REVIEWS_URL = "https://api.outscraper.cloud/google-maps-reviews"

def fetch_reviews(place_id, limit=6):
    """Fetch a handful of recent reviews for context."""
    headers = {"X-API-KEY": OUTSCRAPER_API_KEY}
    params = {"query": place_id, "reviewsLimit": limit, "sort": "newest", "limit": 15, "async": "false", "ignoreEmpty": "true"}
    data = None
    try:
        logger.debug("Fetching %s reviews for place_id=%s", limit, place_id)
        res = requests.get(
            OUTSCRAPER_REVIEWS_URL, headers=headers, params=params, timeout=300
        )
        res.raise_for_status()
        data = res.json()

        logger.info("Fetched reviews for place_id=%s", place_id)
        return data
    except Exception as exc:
        logger.exception("Failed fetching reviews for place_id=%s: %s", place_id, exc)
        return []

class Command(BaseCommand):
    help = "Generate summaries + embeddings using a few live reviews for context"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=25)

    def handle(self, *args, **opts):
        limit = opts["limit"]

        qs = (
            Restaurant.objects.using("gastronet")
            #.filter(place_id__isnull=False)
            .filter(models.Q(summary__isnull=True) | models.Q(summary__exact=""))
            [:limit]
        )

        if not qs.exists():
            self.stdout.write(self.style.NOTICE("No restaurants missing summaries."))
            return

        logger.info("Generating summaries for %s restaurants (limit=%s)", qs.count(), limit)

        for r in qs:
            try:
                # 1. Pull some reviews
                reviews = fetch_reviews(r.place_id, limit=6)
                review_text = "\n\n".join(reviews) if reviews else "No reviews available."

                # 2. Build prompt
                prompt = f"""
                You are a food critic. Summarize this restaurant in under 300 words.
                Focus on the customer experience, atmosphere, and what stands out in reviews services, cuisine.

                Name: {r.name}
                City: {r.city or ''}
                Category: {r.category or r.cuisine or ''}
                Rating: {r.rating or 'N/A'}

                Reviews:
                {review_text}
                """

                # 3. Generate summary
                resp = client.responses.create(model="gpt-5-nano", input=prompt)
                summary = resp.output_text.strip()

                # 4. Generate embedding
                emb = client.embeddings.create(
                    model="text-embedding-3-small", input=summary
                ).data[0].embedding

                # 5. Save
                r.summary = summary
                r.embedding = emb
                r.save(using="gastronet")

                self.stdout.write(self.style.SUCCESS(f"✓ {r.name} summarized with reviews."))

            except Exception as e:
                logger.exception("Failed generating summary for %s", r.name)
                self.stderr.write(self.style.ERROR(f"Error on {r.name}: {e}"))
                continue

        logger.info("Finished generating summaries for %s restaurants", qs.count())
        self.stdout.write(self.style.SUCCESS("✅ Done generating summaries with context."))
