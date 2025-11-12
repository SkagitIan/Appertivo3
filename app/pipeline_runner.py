# onboarding/pipeline_runner.py
from __future__ import annotations
from django.views.decorators.http import require_GET, require_http_methods, require_POST

import logging, json, time, math, re
from datetime import timedelta
from django.db import transaction
import random
from django.utils import timezone
from . import llm, models
from dotenv import load_dotenv
load_dotenv()
import os
import tldextract
logger = logging.getLogger(__name__)
from outscraper import ApiClient
from openai import OpenAI
from urllib.parse import urlparse
from django.shortcuts import render
from django.urls import reverse
# onboarding/views.py
from django.http import JsonResponse
from .models import Onboarding
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404 


def onboarding_status(request, onboarding_id):
    ob = get_object_or_404(Onboarding, uuid=onboarding_id)

    # Compute progress/message/steps however you track state
    progress = ob.progress or 0
    message = ob.current_message or "Collecting your public data and warming up the model…"
    steps = [
        {"label": "Verifying subscription", "done": bool(ob.subscription_ok)},
        {"label": "Crawling website & menus", "done": bool(ob.menus_fetched)},
        {"label": "Indexing Google/Yelp reviews", "done": bool(ob.reviews_indexed)},
        {"label": "Building customer personas", "done": bool(ob.personas_built)},
        {"label": "Generating first concepts", "done": bool(ob.concepts_ready)},
    ]
    is_complete = bool(ob.is_complete)
    is_failed = bool(ob.is_failed)
    restaurant_id = getattr(ob, "restaurant_id", None)

    ctx = {
        "progress": progress,
        "message": message,
        "steps": steps,
        "is_complete": is_complete,
        "is_failed": is_failed,
        "restaurant_id": restaurant_id,
        "onboarding_id": str(ob.pk),
    }

    # Serve JSON for programmatic polling (e.g., Swipe splash)
    wants_json = (
        request.GET.get("format") == "json"
        or "application/json" in (request.headers.get("Accept", ""))
    )
    if wants_json:
        return JsonResponse(ctx)

    # If this is an HTMX poll, render the fragment and use HX-Redirect when done.
    if request.headers.get("HX-Request") == "true":
        response = render(request, "_partials/onboarding_status.html", ctx)
        if is_complete and restaurant_id:
            response["HX-Redirect"] = reverse("dashboard", args=[restaurant_id])
        return response

    # Fallback: render the partial in a bare response for non-HTMX HTML callers
    return render(request, "_partials/onboarding_status.html", ctx)


class OnboardingPipeline:
    """Encapsulates the onboarding process for a restaurant."""

    PROGRESS_MAP = {
        models.Onboarding.State.SCRAPE_DONE: 20,
        models.Onboarding.State.REVIEWS_DONE: 35,
        models.Onboarding.State.WEB_ANALYSIS_DONE: 55,
        models.Onboarding.State.MENU_DONE: 70,
        models.Onboarding.State.REVIEW_ANALYSIS_DONE: 82,
        models.Onboarding.State.PERSONAS_DONE: 92,
        models.Onboarding.State.COMPLETE: 100,
    }

    def __init__(self, onboarding_id):
        logger.info(onboarding_id)
        self.onboarding = (
            models.Onboarding.objects
            .select_related("restaurant", "user")
            .get(uuid=onboarding_id)
        )
        logger.info(self.onboarding)
        self.restaurant = self.onboarding.restaurant
        load_dotenv()
        self.outscraper_client = ApiClient(api_key=os.getenv("OUTSCRAPER_API_KEY"))
        self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # --- Helpers --------------------------------------------------------------
    def now(self):
        return timezone.now()

    def mark_progress(self, state):
        progress = self.PROGRESS_MAP[state]
        if self.onboarding.progress < progress:
            self.onboarding.mark(state, progress=progress)

    def fetch_context(self) -> dict:
        """Fetch Outscraper context for the onboarding restaurant."""
        r = self.restaurant
        if not r:
            logger.warning("Onboarding %s has no restaurant linked", self.onboarding.id)
            return {}

        query = f"{r.name} {r.location_text}".strip()
        if not query:
            logger.info("No valid query for restaurant %s", r)
            return {}

        try:
            results = self.outscraper_client.google_maps_search(
                query,
                limit=1,
                language="en",
                fields=["query","name","place_id","full_address","latitude","longitude","site","phone","type","description","category","subtypes","about","menu_link","order_links", ],
            )
            logger.info(results)
        except Exception as exc:
            logger.warning("Outscraper API call failed for %s: %s", query, exc, exc_info=True)
            return {}

        # SDK returns a list of results; take the first one
        if not results or not isinstance(results, list):
            return {}

        place_info = results[0][0] if results and results[0] else {}

        # Map the response fields into your model
        r.name = place_info.get("name", r.name)
        r.location_text = place_info.get("full_address", "")
        r.google_place_id = place_info.get("place_id", "")
        r.description = place_info.get("description", "")
        r.phone = place_info.get("phone", "")
        r.website = place_info.get("site", "")

        # Save all updates
        r.save(update_fields=[
            "name",
            "location_text",
            "google_place_id",
            "description",
            "phone",
            "website",
        ])
        # persist minimal context
        self.mark_progress(models.Onboarding.State.SCRAPE_DONE)
        self.onboarding.outscraper_data = place_info
        self.onboarding.save(update_fields=["outscraper_data"])
        logger.info("Fetched Outscraper context for %s", r)
        return place_info

    def fetch_reviews(self) -> dict:
        """Start async Google Maps review job and poll until complete."""
        r = self.restaurant
        try:
            # Kick off the async request
            response = self.outscraper_client.google_maps_reviews(
                r.google_place_id,
                limit=20,
                fields=["place_id", "reviews_data"],
                sort="newest",
                ignore_empty="true",
                async_request="true",
            )
            request_id = response.get("id")
            logger.info("Outscraper async started for %s (id=%s)", r.name, request_id)

            # Poll every few seconds until the job is ready
            for attempt in range(20):  # ~60–90 seconds total depending on sleep
                result = self.outscraper_client.get_request_archive(request_id)
                status = (result.get("status") or "").upper()
                logger.info("Polling attempt %d for %s: %s", attempt + 1, r.name, status)

                if status == "SUCCESS":
                    data = result
                    break
                elif status in {"ERROR", "FAILED"}:
                    raise RuntimeError(f"Outscraper returned failure status: {status}")
                time.sleep(5)
            else:
                raise TimeoutError(f"Outscraper polling timed out for {r.name}")

        except Exception as exc:
            logger.warning("Failed to fetch reviews for %s: %s", r.name, exc, exc_info=True)
            data = {"error": str(exc)}

        # Once we have data, persist it
        if data and data.get("status", "").lower() == "success":
            try:
                logger.info(f"POLLED REVIEWS DATA: {data}")

                place = data.get("data", [{}])[0]
                reviews = place.get("reviews_data", [])

                # Store all raw data for debugging / reference
                r.reviews_json = data
                # Generate insights
                logger.info("Wating for markdown")
                r.reviews_markdown = self.llm_clean_response(
                    reviews,
                    "Streamline these reviews for better consumption in an LLM.  Don't do analysis, clean up data, compact it and get it orngaized for an LLM.  consider dates, menu items and scenerios that are of importance.  The data will be used in several analysis."
                )

                # Save only relevant fields
                r.save(update_fields=[
                    "reviews_json",
                    "reviews_markdown",

                ])

                # Sync onboarding record
                self.onboarding.reviews_json = data
                self.onboarding.save(update_fields=["reviews_json"])
                self.mark_progress(models.Onboarding.State.REVIEWS_DONE)

                # Restaurant model no longer stores review_count; use fetched list length
                logger.info("Stored %d reviews for %s", len(reviews or []), r.name)

            except Exception as exc:
                logger.warning("Failed to process review data for %s: %s", r.name, exc, exc_info=True)

        else:
            logger.warning("Outscraper polling returned no usable data: %s", data)

        return data


    def build_web_profile(self) -> dict | None:
        """Analyze the restaurant website and build a structured profile.

        Adds resilient retries for transient OpenAI errors (e.g., 5xx).
        """
        r = self.restaurant

        allowed_domain = self._allowed_domain_from_url(r.website)
        logger.info("Building web profile for domain: %s", allowed_domain or "<none>")

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                profile = self._request_web_profile(
                    allowed_domain=allowed_domain,
                    hint=f"{r.name} official site {r.website}".strip(),
                )
                if not profile:
                    logger.warning(
                        "Empty profile response for %s (%s)", r.name, allowed_domain
                    )
                    return None

                # Generate markdown of the data for future LLM context
                r.websearch_markdown = self.llm_clean_response(
                    profile,
                    "Deepdive knowledge of cuisine type, restaurant style and atmosphere, story, history, everything relevant",
                )

                # Persist to database
                r.websearch_json = json.dumps(profile, indent=2)
                r.menu_json = json.dumps(profile.get("menus", []))
                r.ingredients_json = json.dumps(profile.get("ingredients", []))
                r.save(
                    update_fields=[
                        "websearch_json",
                        "menu_json",
                        "ingredients_json",
                        "websearch_markdown",
                    ]
                )
                self.onboarding.web_profile_json = profile
                self.onboarding.save(update_fields=["web_profile_json"])
                self.mark_progress(models.Onboarding.State.WEB_ANALYSIS_DONE)
                logger.info("Saved web profile for %s", r.name)
                return profile

            except Exception as e:
                # Transient OpenAI errors (e.g., 500/502) are retried with backoff
                logger.exception(
                    "Attempt %d/%d: Error building web profile for %s: %s",
                    attempt,
                    max_attempts,
                    r.name,
                    e,
                )
                if attempt >= max_attempts:
                    # Record last error but do not fail the entire pipeline
                    self.onboarding.mark(
                        self.onboarding.state,
                        error=f"build_web_profile failed: {type(e).__name__}: {e}",
                    )
                    return None
                # Exponential backoff with jitter
                sleep_s = min(20, (2 ** (attempt - 1)) + random.uniform(0, 0.5))
                time.sleep(sleep_s)

    def _allowed_domain_from_url(self, raw_url: str | None) -> str:
        raw_url = (raw_url or "").strip()
        if not raw_url:
            return ""
        extracted = tldextract.extract(raw_url)
        if extracted.domain and extracted.suffix:
            return f"{extracted.domain}.{extracted.suffix}"
        return ""

    def _request_web_profile(self, *, allowed_domain: str | None, hint: str | None = None) -> dict | None:
        prompt = self.web_search_profile_prompt()
        if hint:
            prompt = f"{prompt}\nFocus on this restaurant: {hint}"

        tools = []
        if allowed_domain:
            tools = [
                {
                    "type": "web_search",
                    "filters": {"allowed_domains": [allowed_domain]},
                }
            ]

        response = self.openai_client.responses.create(
            model="gpt-5",
            tools=tools,
            input=prompt,
            text={"format": self.web_search_profile_schema()},
        )
        raw = response.output_text or ""
        return json.loads(raw) if raw else None

    def build_competitive_analysis(self) -> dict | None:
        r = self.restaurant
        subject_context = self._subject_context_snapshot()
        query = self._derive_competitor_query(subject_context)
        if not query:
            logger.info("Skipping competitive analysis for %s; no query available", r.name)
            return None

        logger.info("Running competitive analysis for %s with query '%s'", r.name, query)
        try:
            raw_results = self.outscraper_client.google_maps_search(
                query,
                limit=15,
                language="en",
                fields=[
                    "query",
                    "name",
                    "place_id",
                    "full_address",
                    "latitude",
                    "longitude",
                    "site",
                    "phone",
                    "type",
                    "types",
                    "description",
                    "category",
                    "subtypes",
                    "rating",
                    "reviews",
                    "about",
                ],
            )
        except Exception as exc:
            logger.warning("Outscraper competitor search failed for %s: %s", r.name, exc, exc_info=True)
            return None

        candidates = self._normalize_outscraper_search_results(
            raw_results,
            subject_place_id=r.google_place_id,
            subject_coords=(subject_context.get("latitude"), subject_context.get("longitude")),
        )

        if not candidates:
            logger.info("No competitive candidates found for %s", r.name)
            return None

        shortlisted = self._select_competitors(subject_context, candidates)
        if not shortlisted:
            logger.info("LLM did not select competitors for %s", r.name)
            return None

        competitor_entries = [self._build_competitor_entry(comp) for comp in shortlisted]
        analysis_text = self._analyze_competitors(subject_context, competitor_entries)

        payload = {
            "subject": subject_context,
            "query": query,
            "candidates": candidates,
            "competitors": competitor_entries,
            "analysis_markdown": analysis_text,
        }

        r.competitive_analysis = payload
        r.save(update_fields=["competitive_analysis"])
        self.onboarding.competitive_analysis = payload
        self.onboarding.save(update_fields=["competitive_analysis"])
        logger.info("Competitive analysis saved for %s", r.name)
        return payload

    def _subject_context_snapshot(self) -> dict:
        r = self.restaurant
        outscraper = self.onboarding.outscraper_data or {}
        web_profile = self.onboarding.web_profile_json or {}
        location_text = r.location_text or outscraper.get("full_address") or ""
        city, region = self._extract_city_region(location_text)

        primary_style = (
            web_profile.get("style_vibe")
            or outscraper.get("category")
            or outscraper.get("type")
            or "restaurant"
        )

        cuisine_tags = []
        subtypes = outscraper.get("subtypes") or []
        if isinstance(subtypes, list):
            cuisine_tags.extend([s for s in subtypes if s])

        latitude = self._safe_float(r.latitude) or self._safe_float(outscraper.get("latitude"))
        longitude = self._safe_float(r.longitude) or self._safe_float(outscraper.get("longitude"))

        return {
            "name": r.name,
            "location_text": location_text,
            "city": city,
            "region": region,
            "primary_style": primary_style,
            "cuisine_tags": cuisine_tags,
            "website": r.website,
            "latitude": latitude,
            "longitude": longitude,
            "description": r.description or outscraper.get("description"),
        }

    def _derive_competitor_query(self, subject: dict) -> str:
        location_parts = [p for p in [subject.get("city"), subject.get("region")] if p]
        location_str = " ".join(location_parts).strip() or (subject.get("location_text") or "")
        descriptor_candidates = [subject.get("primary_style")]
        cuisine_tags = subject.get("cuisine_tags") or []
        if cuisine_tags:
            descriptor_candidates.append(cuisine_tags[0])
        descriptor_candidates.append("restaurant")
        descriptor = " ".join([d for d in descriptor_candidates if d]).strip()
        if not descriptor or not location_str:
            return ""
        return f"{descriptor} in {location_str}".strip()

    def _normalize_outscraper_search_results(self, raw_results, subject_place_id: str | None, subject_coords: tuple[float | None, float | None]) -> list:
        candidates = []
        seen = set()
        lat1, lon1 = subject_coords

        for entry in self._iter_outscraper_places(raw_results):
            if not isinstance(entry, dict):
                continue
            place_id = entry.get("place_id") or entry.get("google_id")
            if not place_id or place_id == subject_place_id or place_id in seen:
                continue

            latitude = self._safe_float(entry.get("latitude"))
            longitude = self._safe_float(entry.get("longitude"))
            distance = self._distance_miles(lat1, lon1, latitude, longitude)

            candidate = {
                "place_id": place_id,
                "name": entry.get("name"),
                "full_address": entry.get("full_address") or entry.get("address") or entry.get("vicinity"),
                "site": entry.get("site"),
                "phone": entry.get("phone"),
                "types": self._normalize_types(entry),
                "category": entry.get("category"),
                "description": entry.get("description") or entry.get("about"),
                "rating": self._safe_float(entry.get("rating")),
                "total_reviews": self._safe_int(entry.get("reviews")),
                "latitude": latitude,
                "longitude": longitude,
                "distance_miles": distance,
            }
            candidates.append(candidate)
            seen.add(place_id)

        candidates.sort(
            key=lambda c: (
                c["distance_miles"] is None,
                c["distance_miles"] or float("inf"),
                -(c.get("rating") or 0),
            )
        )
        return candidates

    def _iter_outscraper_places(self, raw_results):
        if isinstance(raw_results, list):
            for entry in raw_results:
                yield from self._iter_outscraper_places(entry)
            return

        if isinstance(raw_results, dict):
            data = (
                raw_results.get("data")
                or raw_results.get("results")
                or raw_results.get("places")
                or raw_results.get("items")
            )
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        yield item
            else:
                yield raw_results

    def _distance_miles(self, lat1, lon1, lat2, lon2):
        if None in (lat1, lon1, lat2, lon2):
            return None
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        earth_radius_miles = 3958.8
        return round(earth_radius_miles * c, 2)

    def _select_competitors(self, subject: dict, candidates: list, max_competitors: int = 5) -> list:
        if not candidates:
            return []

        candidate_payload = [
            {
                "place_id": c.get("place_id"),
                "name": c.get("name"),
                "address": c.get("full_address"),
                "distance_miles": c.get("distance_miles"),
                "category": c.get("category"),
                "types": c.get("types"),
                "rating": c.get("rating"),
                "total_reviews": c.get("total_reviews"),
            }
            for c in candidates
        ]

        try:
            response = self.openai_client.responses.create(
                model="gpt-5-mini",
                input=(
                    f"{self.competitor_selection_prompt()}\n"
                    f"Subject: {json.dumps(subject, ensure_ascii=False)}\n"
                    f"Candidates: {json.dumps(candidate_payload, ensure_ascii=False)}"
                ),
                text={"format": self.competitor_selection_schema()},
            )
            raw = response.output_text or ""
            picked = json.loads(raw).get("competitors", []) if raw else []
        except Exception as exc:
            logger.warning("Competitor selection LLM failed: %s", exc, exc_info=True)
            picked = []

        lookup = {c.get("place_id"): c for c in candidates if c.get("place_id")}
        shortlist = []
        for pick in picked:
            place_id = pick.get("place_id")
            base = lookup.get(place_id)
            if not base:
                continue
            enriched = base.copy()
            enriched["selection_reason"] = pick.get("reason")
            enriched["selection_score"] = pick.get("similarity_score")
            enriched["selection_distance_override"] = pick.get("distance_miles")
            shortlist.append(enriched)
            if len(shortlist) >= max_competitors:
                break

        if not shortlist:
            shortlist = candidates[:max_competitors]
        return shortlist

    def competitor_selection_prompt(self) -> str:
        return (
            "Select up to five restaurants that most directly compete with the subject restaurant. "
            "Prioritize geographic proximity (<=20 miles when possible) and overlapping cuisine or service style. "
            "Avoid chains unless the subject is also a chain. Explain why each pick is competitive."
        )

    def competitor_selection_schema(self):
        return {
            "name": "competitor_shortlist",
            "type": "json_schema",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "competitors": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "place_id": {"type": "string"},
                                "reason": {"type": "string"},
                                "similarity_score": {"type": "number"},
                                "distance_miles": {"type": ["number", "null"]},
                            },
                            "required": ["place_id", "reason", "similarity_score"],
                        },
                    }
                },
                "required": ["competitors"],
            },
        }

    def _build_competitor_entry(self, comp: dict) -> dict:
        entry = comp.copy()
        entry["profile"] = None
        entry["profile_trimmed"] = None
        entry["profile_summary"] = ""
        site = comp.get("site")
        if not site:
            return entry

        allowed_domain = self._allowed_domain_from_url(site)
        if not allowed_domain:
            return entry

        try:
            profile = self._request_web_profile(
                allowed_domain=allowed_domain,
                hint=f"{comp.get('name')} competitor site {site}",
            )
            entry["profile"] = profile
            if profile:
                entry["profile_trimmed"] = self._trim_profile_for_analysis(profile)
                entry["profile_summary"] = self.llm_clean_response(
                    {"outscraper": comp, "profile": entry["profile_trimmed"]},
                    f"Summarize why {comp.get('name')} competes with the subject restaurant.",
                )
        except Exception as exc:
            logger.warning("Failed to build competitor profile for %s: %s", comp.get("name"), exc, exc_info=True)

        return entry

    def _trim_profile_for_analysis(self, profile: dict | None) -> dict | None:
        if not profile:
            return None
        menu_sections = []
        for section in (profile.get("menus") or [])[:3]:
            items = section.get("items") or []
            menu_sections.append(
                {
                    "section": section.get("section"),
                    "sample_items": [item.get("name") for item in items[:5] if item.get("name")],
                }
            )
        return {
            "style_vibe": profile.get("style_vibe"),
            "menu_sections": menu_sections,
            "ingredients": (profile.get("ingredients") or [])[:15],
            "personas": profile.get("personas"),
            "contact": profile.get("contact"),
        }

    def _analyze_competitors(self, subject: dict, competitors: list) -> str:
        if not competitors:
            return ""

        analysis_payload = []
        for comp in competitors:
            analysis_payload.append(
                {
                    "name": comp.get("name"),
                    "distance_miles": comp.get("distance_miles"),
                    "selection_reason": comp.get("selection_reason"),
                    "profile_summary": comp.get("profile_summary"),
                    "style_vibe": (comp.get("profile") or {}).get("style_vibe") if comp.get("profile") else None,
                    "menu_sections": (comp.get("profile_trimmed") or {}).get("menu_sections") if comp.get("profile_trimmed") else None,
                    "ingredients": (comp.get("profile_trimmed") or {}).get("ingredients") if comp.get("profile_trimmed") else None,
                }
            )

        try:
            response = self.openai_client.responses.create(
                model="gpt-5",
                input=(
                    f"{self.competitive_analysis_prompt()}\n"
                    f"Subject: {json.dumps(subject, ensure_ascii=False)}\n"
                    f"Competitors: {json.dumps(analysis_payload, ensure_ascii=False)}"
                ),
            )
            return response.output_text or ""
        except Exception as exc:
            logger.warning("Competitive analysis write-up failed: %s", exc, exc_info=True)
            return ""

    def competitive_analysis_prompt(self) -> str:
        return (
            "You are an F&B strategy analyst. Compare the subject restaurant to the shortlisted competitors. "
            "Highlight style overlaps, pricing/occasion positioning, signature menu moves, and whitespace opportunities. "
            "Close with tactical recommendations for how the subject can differentiate."
        )

    def _extract_city_region(self, location_text: str | None) -> tuple[str | None, str | None]:
        if not location_text:
            return None, None
        parts = [p.strip() for p in re.split(r",|\n", location_text) if p.strip()]
        if not parts:
            return None, None
        if len(parts) == 1:
            return parts[0], None
        return parts[-2], parts[-1]

    def _safe_float(self, value):
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _safe_int(self, value):
        try:
            if value is None:
                return None
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _normalize_types(self, entry: dict) -> list:
        for key in ("subtypes", "types", "type", "category"):
            raw = entry.get(key)
            if isinstance(raw, list):
                cleaned = [str(item).strip() for item in raw if item]
                if cleaned:
                    return cleaned
            elif isinstance(raw, str) and raw.strip():
                return [raw.strip()]
        return []

    def run_review_analysis(self) -> dict:
        """Summarize restaurant reviews using OpenAI, with local fallback."""
        reviews = self.restaurant.reviews_markdown
        try:
            started = time.monotonic()
            response = self.openai_client.responses.create(
                model="gpt-5-nano",
                input=f"{self.review_analysis_prompt()} Here are the reviews: {reviews}",
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            data = response.output_text or ""

            logger.info("Review analysis complete (%d reviews, %d mexits)", len(reviews), latency_ms)

            # persist to both restaurant and onboarding
            self.restaurant.review_analysis = data
            self.restaurant.save(update_fields=["review_analysis"])
            self.onboarding.review_analysis = data
            self.onboarding.save(update_fields=["review_analysis"])
            self.mark_progress(models.Onboarding.State.REVIEW_ANALYSIS_DONE)
            return data

        except Exception as exc:
            logger.warning("Review analysis failed: %s", exc, exc_info=True)
            fallback = {"sentiment": "neutral", "average_rating": None, "themes": [], "highlights": []}
            r.review_analysis = fallback
            r.save(update_fields=["review_analysis"])
            self.onboarding.review_analysis_json = fallback
            self.onboarding.save(update_fields=["review_analysis"])
            return fallback

    def generate_personas(self):
        r = self.restaurant
        try:
            started = time.monotonic()
            response = self.openai_client.responses.create(
                model="gpt-5",
                input=f"{self.personas_analysis_prompt()} Here is some background on the restaurant:  {r.websearch_json} Here are the reviews: {json.dumps(r.reviews_json)} here is the menu{r.menu_json}",
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            data = response.output_text or ""

            logger.info("Review analysis complete ")

            # persist to both restaurant and onboarding
            r.personas = data
            r.save(update_fields=["personas"])
            self.onboarding.personas_analysis = data
            self.onboarding.save(update_fields=["personas_analysis"])
            self.mark_progress(models.Onboarding.State.PERSONAS_DONE)
            return data

        except Exception as exc:
            logger.warning("personas analysis failed: %s", exc, exc_info=True)
            fallback = "No personas were developed."
            r.personas = fallback
            r.save(update_fields=["personas"])
            self.onboarding.personas_analysis = fallback
            self.onboarding.save(update_fields=["personas_analysis"])
            return fallback

    def llm_clean_response(self, response_json: dict, task: str) -> str:
        """
        Uses a lightweight OpenAI model to convert a raw JSON response into
        task-relevant, LLM-ready markdown context.
        """
        system = (
            "You are a data cleaner preparing context for a larger AI system. "
            "Given JSON data, extract only fields that are useful for the task, "
            "normalize names, and output clean, concise Markdown with clear section headings. "
            "Do not invent or summarize beyond the data provided."
        )

        response = self.openai_client.responses.create(
                model="gpt-5-nano",
                input=f"""
                {system} \n
                Here is the task where the data wil be used:  {task}\n
                The data to analyze: {json.dumps(response_json, indent=2)}
                """,
            )
        return response.output_text


    def finalize(self):
        """Finalize settings and mark complete."""
        settings_obj, _ = models.RestaurantSettings.objects.get_or_create(
            restaurant=self.restaurant,
            defaults={"default_currency": self.onboarding.default_currency},
        )
        self.mark_progress(models.Onboarding.State.COMPLETE)

    # --- Orchestration -------------------------------------------------------

    def run_all(self):
        """Run all onboarding steps sequentially."""
        steps = [
            self.fetch_context,
            self.fetch_reviews,
            self.run_review_analysis,
            self.build_web_profile,
            self.build_competitive_analysis,
            self.generate_personas,
            self.finalize,
        ]
        for step in steps:
            try:
                with transaction.atomic():
                    result = step()
                    if result is None:
                        logger.info("Step %s skipped or no-op", step.__name__)
                    else:
                        logger.info("Step %s complete", step.__name__)
            except Exception as e:
                logger.exception("Step %s failed", step.__name__)
                self.onboarding.fail(str(e))
                break


    # schema and prompts
    def personas_analysis_prompt(self):
        prompt = f"""
            You are a culinary marketing and menu development expert. You specialize in transforming restaurant data (reviews, menus, descriptions) into clear, realistic customer personas that can guide creative decisions in menu design, pricing, and presentation.

            Your output should emphasize motivations, emotional drivers, and ordering behaviors rather than demographics alone.

            User Instruction:
            Given the following restaurant data, generate three distinct customer personas that would help guide menu development decisions.

            Each persona should include:

            Name & summary title (short, human-sounding label like “The Local Loyalist” or “Adventurous Date-Night Duo”)

            Core motivations (why they visit this restaurant)

            Dining habits & preferences (menu patterns, price sensitivity, frequency, group type)

            Emotional tone (what they value—comfort, novelty, authenticity, convenience, etc.)

            Key menu insights (what this persona implies for future menu design—e.g., “They’d respond well to chef’s tasting options” or “Would benefit from more hearty vegetarian dishes”)

            Input:

            Restaurant Description:
            {self.restaurant.description}

            Sample Menu Data:
            {self.restaurant.menu_json}

            Customer Reviews:
            {self.restaurant.reviews_json}
        """
        return prompt

    def review_analysis_prompt(self):
        prompt = (
            "You are analyzing restaurant customer reviews."
            "Return JSON with keys: sentiment (positive/neutral/negative), "
            "average_rating (number), themes (array of short strings), and "
            "highlights (array of <=3 review snippets)."
        )
        return prompt

    def web_search_profile_prompt(self):
        prompt = f"""
            You are a precise restaurant analyst. Use the web_search tool to thoroughly explore the restaurant’s site and any directly linked pages/PDFs within allowed_domains only.

            GOALS
            1) Atmosphere & Identity
            • Describe the restaurant’s style, aesthetic, ambiance, and brand personality (concise, vivid).

            2) Menu Links & Structure
            • Collect ALL menu URLs (HTML, PDFs, embeds).
            • For each menu section, list items with: name, description, price_cents (integer or null), currency (ISO code or null), allergens (array).
            • Provide a section-level source_url (page or PDF URL where the section was found).

            3) Contact & Operational
            • phone, email, address (strings).
            • reservation_url (string; empty string if not present).
            • social_links (array of absolute URLs; empty if none).

            4) Personas (EXACTLY THREE)
            • Return an array of exactly 3 paragraphs (strings).
            • Each paragraph is 2–4 sentences describing a distinct guest persona grounded in site evidence (and reviews if linked).

            5) Master Ingredient List
            • Parse all menu item names and descriptions to extract ingredients.
            • Normalize to singular, lowercase US spelling.
            • Return ONLY a de-duplicated array of ingredient names (strings).

            RULES
            • Stay within allowed_domains = Absolute URLs only.
            • If a field is unknown, still include it with the correct empty value type.
            • Return ONLY valid JSON conforming exactly to the schema named “restaurant_profile”.
            """
        return prompt

    def web_search_profile_schema(self):
        schema = {
            "name": "restaurant_profile",
            "type": "json_schema",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "style_vibe": {"type": "string"},
                    "menu_urls": {"type": "array", "items": {"type": "string"}},
                    "menus": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "section": {"type": "string"},
                                "source_url": {"type": "string"},
                                "items": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "properties": {
                                            "name": {"type": "string"},
                                            "description": {"type": "string"},
                                            "price_cents": {"type": ["integer", "null"]},
                                            "currency": {"type": ["string", "null"]},
                                            "allergens": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                        },
                                        "required": [
                                            "name",
                                            "description",
                                            "price_cents",
                                            "currency",
                                            "allergens",
                                        ],
                                    },
                                },
                            },
                            "required": ["section", "source_url", "items"],
                        },
                    },
                    "contact": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "phone": {"type": "string"},
                            "email": {"type": "string"},
                            "address": {"type": "string"},
                            "reservation_url": {"type": "string"},
                            "social_links": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": [
                            "phone",
                            "email",
                            "address",
                            "reservation_url",
                            "social_links",
                        ],
                    },
                    "personas": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {"type": "string"},
                    },
                    "ingredients": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "style_vibe",
                    "menu_urls",
                    "menus",
                    "contact",
                    "personas",
                    "ingredients",
                ],
            },
        }
        return schema
