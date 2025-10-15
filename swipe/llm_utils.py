"""Helpers for talking to LLM services.

The helpers in this module call out to third party APIs when keys are
configured and otherwise fall back to deterministic placeholder data so
tests can run without network access.
"""

import os
import asyncio
import threading
import cloudinary
from openai import AsyncOpenAI
from replicate import Client as ReplicateClient
from django.utils import timezone
from swipe.models import Concept, Dish
from dotenv import load_dotenv
load_dotenv()
import datetime
import cloudinary.uploader
import logging
import uuid
logger = logging.getLogger(__name__)
import json

class GetConcepts:
    """
    Generates and optionally saves 3 concepts (each with 3 dishes).
    Handles initialization of OpenAI, Replicate, and Cloudinary clients.
    """

    def __init__(self, restaurant=None):
        # --- Environment setup ---
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.replicate_token = os.getenv("REPLICATE_API_KEY")
        self.cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
        self.cloud_key = os.getenv("CLOUDINARY_API_KEY")
        self.cloud_secret = os.getenv("CLOUDINARY_SECRET_KEY")

        # --- Client initialization ---
        self.openai_client = (
            AsyncOpenAI(api_key=self.openai_api_key) if self.openai_api_key else None
        )
        self.replicate_client = ReplicateClient(api_token=self.replicate_token) if self.replicate_token else None

        # --- Cloudinary configuration ---
        if all([self.cloud_name, self.cloud_key, self.cloud_secret]):
            cloudinary.config(
                cloud_name=self.cloud_name,
                api_key=self.cloud_key,
                api_secret=self.cloud_secret,
            )

        # --- Model & defaults ---
        self.REPLICATE_MODEL = (
            "prunaai/flux.1-dev:b0306d92aa025bb747dc74162f3c27d6ed83798e08e5f8977adf3d859d0536a3"
        )
        self.DEFAULT_DISH_IMAGE_URL = "https://placehold.co/800x600?text=Dish"
        self.DEFAULT_CONCEPT_IMAGE_URL = "https://placehold.co/1200x800?text=Concept"

        self.restaurant = restaurant
        self.locale_summary = None
        threading.Thread(target=self._load_locale, daemon=True).start()


    # -----------------------------
    # 🧠 Concept generation
    # -----------------------------
    async def generate_batch(self):
        """Generate three concepts (each with dishes and sketches) concurrently."""

        concepts = await self._generate_concepts()
        if not concepts:
            logger.info("No concepts returned from OpenAI.")
            return []

        tasks = [self._process_single_concept(concept) for concept in concepts]
        processed = await asyncio.gather(*tasks)
        results = [result for result in processed if result]
        logger.info("All concepts and dishes generated and saved.")
        return results

    async def _process_single_concept(self, concept_data):
        concept_payload = dict(concept_data)

        try:
            sketch_task = asyncio.create_task(self._generate_sketch(concept_payload))
            dishes_task = asyncio.create_task(self._generate_dishes_for_concept(concept_payload))
            sketch_url, dishes = await asyncio.gather(sketch_task, dishes_task)

            concept_payload["sketch_url"] = sketch_url or self.DEFAULT_CONCEPT_IMAGE_URL

            concept_obj = await self._create_concept_record(concept_payload)
            concept_id = concept_obj.pk
            if isinstance(concept_id, uuid.UUID):
                concept_id = str(concept_id)

            saved_dishes = await self._save_dishes(concept_obj, dishes)

            logger.info("Concept '%s' and dishes saved successfully.", concept_payload.get("title", ""))
            return {
                "id": concept_id,
                "restaurant_id": self.restaurant.id if self.restaurant else None,
                "name": concept_payload.get("title", ""),
                "subtitle": concept_payload.get("subtitle", ""),
                "sketch_url": concept_payload.get("sketch_url", ""),
                "tags": concept_payload.get("tags", []),
                "ideal_dishes": concept_payload.get("ideal_dishes", ""),
                "reasoning": concept_payload.get("reasoning", ""),
                "dishes": saved_dishes,
            }
        except Exception as exc:
            logger.warning(
                "Concept processing failed for %s: %s",
                concept_payload.get("title", "unknown"),
                exc,
            )
            return None

    async def _create_concept_record(self, concept_payload):
        if not self.restaurant:
            raise ValueError("Restaurant context is required to save concepts.")

        def create():
            reasoning = concept_payload.get("reasoning", "")
            ideal_dishes = concept_payload.get("ideal_dishes", "")
            meta_reasoning = f"{reasoning}\n\nIdeal dishes: {ideal_dishes}".strip()

            return Concept.objects.create(
                restaurant=self.restaurant,
                name=concept_payload.get("title", ""),
                subtitle=concept_payload.get("subtitle", ""),
                sketch_url=concept_payload.get("sketch_url", ""),
                meta_ingredients=concept_payload.get("tags", []),
                meta_reasoning=meta_reasoning,
                created_at=timezone.now(),
            )

        return await asyncio.to_thread(create)

    async def append_dishes_to_concept(self, concept):
        """Generate and append a new set of dishes for an existing concept."""

        if isinstance(concept, Concept):
            concept_obj = concept
        else:
            concept_obj = await asyncio.to_thread(Concept.objects.get, pk=concept)

        concept_payload = self._normalize_concept(concept_obj)
        dishes = await self._generate_dishes_for_concept(concept_payload)
        saved_dishes = await self._save_dishes(concept_obj, dishes)
        logger.info("Appended %s dishes to concept '%s'.", len(saved_dishes), concept_obj.name)
        return saved_dishes


    # -----------------------------
    # 🧩 Helpers
    # -----------------------------
    def _load_locale(self):
        if not self.openai: 
            return
        loc, name = self.restaurant["location"], self.restaurant["name"]
        date = datetime.now().strftime("%A, %B %d, %Y")

        prompt = f"""
        You are a food writer describing today’s local atmosphere for {name} in {loc} on {date}.
        Mention current season, weather mood, and local ingredients in under 100 words.
        """

        try:
            resp = self.openai.responses.create(
                model="gpt-5-nano-2025-08-07",
                reasoning={"effort": "minimal"},
                input=prompt,
            )
            self.locale_summary = resp.output_text.strip()
        except Exception as e:
            print("⚠️ Locale error:", e)
            
    async def _generate_concepts(self):
        """Call OpenAI once to generate three structured concepts."""

        if not self.openai_client:
            logger.info("OpenAI client not configured; returning no concepts.")
            return []

        response = await self.openai_client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "system",
                    "content": self.concept_prompt(),
                },
                {"role": "user", "content": self.restaurant.context+self.locale_summary},
            ],
            text={"format": self.concept_schema()},
        )

        data = json.loads(response.output[0].content[0].text)
        logger.info("Concept OpenAI response: %s", data)
        return data.get("concepts", [])

    def concept_prompt(self):
        prompt = f"""
                **Role**: You are a seasoned restaurant marketing consultant with deep knowledge of regional cuisines, seasonal ingredients, and cultural dining traditions.
                **Task**: Generate exactly 3 unique, theme-based concepts for daily specials that emphasize regional flavors and seasonal ingredients.

                **Format Requirements for Each Concept**:
                - **Name**: Maximum 30 characters
                - **Subtitle**: Maximum 80 characters (descriptive tagline)
                - **ideal_dishes** Maximum 200 characters
                - **Reasoning**: Explain your creative process and mindset when selecting this concept (maximum 80 characters)
                - **Tags**: Array of 3 relevant keywords that connect the concept to user context
                - **Sketch Prompt": generate a prompt that instructs an llm to create a sketch of the concept to be used as bckground art for the concept card.

                **Concept Guidelines**:
                - It should be relevant to the users restaurants menu, not identical but within the same style.
                - Focus on THEMES, not individual dishes (like "Taco Tuesday" or "Mediterranean Monday")
                - Emphasize regional specialties around: {self.restaurant.location_text} 
                - and seasonal ingredients: {datetime.date.today()}
                - Consider cultural celebrations, harvest seasons, and local food traditions
                - Think beyond basic concepts to include:
                - Regional American cuisines (Southern, Pacific Northwest, Southwest, etc.)
                - Seasonal produce celebrations (Spring asparagus, Fall harvest, Summer stone fruits)
                - Cultural heritage nights (Italian Nonna Night, Korean Comfort, etc.)
                - Weather-responsive themes (Cozy Soup Sundays, Summer Grill Nights)


                **Example Structure**:
                ```
                Name: “Harvest Moon Monday”
                Subtitle: “Celebrating autumn's bounty with locally-sourced seasonal ingredients”
                ideal_dishes: “Roasted squash bisque with sage cream, cider-braised pork shoulder, apple-pear galette with honey drizzle”
                Reasoning: “Captured the cozy autumn feeling and farm-to-table movement.”
                Tags: [seasonal, autumn, local-sourcing, comfort-food, farm-to-table, harvest, cozy, regional]

                Name: “Coastal Catch Tuesday”
                Subtitle: “Showcasing the freshest seafood from our local waters”
                ideal_dishes: “Pan-seared halibut with lemon-herb butter, Dungeness crab cakes, sea-salt caramel panna cotta”
                Reasoning: “Leans into coastal identity and freshness; ideal for restaurants near bays or rivers.”
                Tags: [seafood, coastal, local, freshness, sustainability, light-fare, summer, maritime]

                Name: “Woodfire Wednesday”
                Subtitle: “Rustic warmth and smoke-kissed flavor straight from the hearth”
                ideal_dishes: “Wood-grilled flat iron steak with rosemary potatoes, charred vegetable medley, smoked chocolate mousse”
                Reasoning: “Centers on elemental cooking and the sensory experience of fire.”
                Tags: [grill, rustic, smoky, comfort-food, dinner, artisan, bold-flavors, midweek-special]

                Name: “Garden Glow Thursday”
                Subtitle: “A vibrant vegetarian spread celebrating color, texture, and balance”
                ideal_dishes: “Roasted beet and citrus salad, mushroom risotto with truffle oil, lavender panna cotta”
                Reasoning: “Brings visual appeal and wellness focus; ideal for health-conscious diners.”
                Tags: [vegetarian, seasonal, healthy, colorful, light, sustainable, spring, garden-to-table]

                Name: “Fireside Friday”
                Subtitle: “Hearty fare and nostalgic comfort to welcome the weekend”
                ideal_dishes: “Short rib pot pie with puff pastry lid, smoked cheddar mac & cheese, bourbon bread pudding”
                Reasoning: “Invites end-of-week indulgence and evokes cozy camaraderie.”
                Tags: [comfort-food, weekend, hearty, indulgent, nostalgic, winter, fireside, crowd-pleaser]
                ```

                **Goal**: Create concepts that restaurant owners can easily adapt to their local region and seasonal availability while building customer excitement and loyalty.

            """
        return prompt

    def concept_schema(self):
        schema = {
                "name": "concept_list",
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {
                        "concepts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string",},
                                    "subtitle": {"type": "string"},
                                    "ideal_dishes": {"type": "string"},
                                    "reasoning": {"type": "string" },
                                    "tags": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "minItems": 1,
                                        "maxItems": 3
                                    }
                                },
                                "required": ["title", "subtitle", "reasoning", "tags","ideal_dishes"],
                                "additionalProperties": False
                            },
                            "minItems": 3,
                            "maxItems": 3
                        }
                    },
                    "required": ["concepts"],
                    "additionalProperties": False,
                },
                "strict": True,
            }
        return schema

    def _normalize_concept(self, concept):
        """
        Coerce concept payloads (dicts from the LLM or Concept instances) into a shared structure.
        """
        if isinstance(concept, dict):
            normalized = {
                "title": concept.get("title") or concept.get("name", ""),
                "subtitle": concept.get("subtitle", ""),
                "reasoning": concept.get("reasoning", ""),
                "tags": concept.get("tags", []),
                "ideal_dishes": concept.get("ideal_dishes", ""),
            }
            # Preserve any additional keys (e.g., sketch_url) so callers can still access them.
            normalized.update({k: v for k, v in concept.items() if k not in normalized})
            return normalized

        if isinstance(concept, Concept):
            return {
                "title": concept.name,
                "subtitle": concept.subtitle or "",
                "reasoning": concept.meta_reasoning or "",
                "tags": concept.meta_ingredients or [],
                "ideal_dishes": "",
                "sketch_url": concept.sketch_url or "",
            }

        raise TypeError(f"Unsupported concept payload: {type(concept)!r}")

    async def _save_dishes(self, concept_obj, dishes):
        """Persist dishes for the provided concept, generating images concurrently."""

        if not dishes:
            return []

        image_results = await asyncio.gather(
            *(self._generate_image(dish) for dish in dishes),
            return_exceptions=True,
        )

        dish_objects = []
        saved_payloads = []
        for dish, image_result in zip(dishes, image_results):
            if isinstance(image_result, Exception):
                logger.warning(
                    "Image failed for %s: %s",
                    dish.get("title", "unknown"),
                    image_result,
                )
                image_url = self.DEFAULT_DISH_IMAGE_URL
            else:
                image_url = image_result or self.DEFAULT_DISH_IMAGE_URL

            dish_objects.append(
                Dish(
                    concept=concept_obj,
                    name=dish["title"],
                    reasoning=dish.get("description", ""),
                    ingredients=dish.get("ingredient_overlap", []),
                    price=dish.get("suggested_price", ""),
                    image_url=image_url,
                )
            )
            saved_payloads.append({**dish, "image_url": image_url})

        concept_id = concept_obj.pk
        if isinstance(concept_id, uuid.UUID):
            concept_id = str(concept_id)

        def bulk_create():
            return Dish.objects.bulk_create(dish_objects)

        created_dishes = await asyncio.to_thread(bulk_create)

        for payload, dish_obj in zip(saved_payloads, created_dishes):
            dish_id = dish_obj.pk
            if isinstance(dish_id, uuid.UUID):
                dish_id = str(dish_id)
            payload.update({
                "id": dish_id,
                "concept_id": concept_id,
            })

        return saved_payloads

    async def _generate_sketch(self, c) -> str:
        """Generate and upload a sketch image for the given concept."""

        if not self.replicate_client:
            return ""

        sketch_prompt = f"""
            Create a high-definition monochrome pencil sketch that captures the culinary spirit of "{c["title"]}".

            Concept subtitle: "{c.get("subtitle", "")}" 

            Let the sketch interpret this concept through visual metaphors drawn from food, craft, and preparation.
            Focus on textures, ingredients, and the rhythm of a working kitchen — gestures, utensils, cookware, or produce
            that echo the mood behind the idea. Draw inspiration from these guiding notes: {c.get("tags", []) }.

            This concept is described as: "{c['reasoning']}"
            The dishes envisioned for it include: {c['ideal_dishes']}.

            Keep the composition minimalist and tonal — pencil or graphite only, no color, no text, no people, no branding.
            Think of it as a chef’s visual brainstorm, a vignette of creativity and craft rather than a finished dish.
            The art should suggest aroma, movement, and imagination within the world of {c["title"]}.
        """.strip()

        # --- Generate image with Replicate ---
        try:
            output = await asyncio.to_thread(
                self.replicate_client.run,
                self.REPLICATE_MODEL,
                input={
                    "prompt": sketch_prompt+self.locale_summary,
                    "output_format": "png",
                    "output_quality": 100,
                },
            )
        except Exception as exc:
            logger.warning("Replicate generation failed: %s", exc, exc_info=True)
            return ""

        # --- Upload first image URL from Replicate to Cloudinary ---
        try:
            replicate_url = output[0] if isinstance(output, list) else output
            if not replicate_url:
                return ""
            upload_result = await asyncio.to_thread(
                cloudinary.uploader.upload,
                replicate_url,
                folder="concept_sketches",
                public_id=str(uuid.uuid4()),
                overwrite=True,
                resource_type="image",
            )
            final_url = upload_result.get("secure_url", "")
            logger.info("Sketch uploaded for: %s", final_url)
            return final_url
        except Exception as exc:
            logger.warning("Cloudinary upload failed: %s", exc, exc_info=True)
            return ""

    def dish_prompt(self, c):
        prompt = f"""
            Given the following restaurant profile and concept, generate three (3) saleable dish ideas that fit seamlessly within the restaurant’s 
            current culinary voice, audience, and menu architecture.

            Restaurant Context:
            {self.restaurant.context}

            Concept:
            {c["title"]} — {c.get("subtitle", "")}
            {c['reasoning']}
  

            Instructions for Generation:
            Stay in voice: Dishes should feel native to the restaurant—premium comfort with Pacific Northwest ingredients and steakhouse warmth.
            Anchor in reality: Use ingredients already found across the restaurant’s menu for continuity (refer to Key Ingredients and overlapping items).
            Invent, don’t repeat: Dishes must be fresh additions or seasonal riffs on existing hits (e.g. a fall version of short ribs or salmon).
            Highlight saleability: Each dish should sound craveable, cost-balanced, and easy for the kitchen to execute with existing prep lines.
            Focus on story: Each description should tie emotionally to the concept subtitle—regional, seasonal, or nostalgic cues.
            Balance variety: Include one protein-forward entrée, one seafood or salad-leaning option, and one bar-friendly or shared plate.
            Be concise: Limit each description to 40–60 words; vivid but menu-ready.
            ingredient_overlap: List up to 5 existing ingredients the new dish would share with the current menu (use lowercase, comma-separated).
            category_tags: 3–4 descriptors combining dish type and theme (e.g. ["entree", "beef", "comfort", "fall"]).
            analyze menu pricing, ingredient usage, trends and locale to genreate a suggested price for the dish.
            Example tone (not to be copied):
            “Smoked Maple Ribeye Tips — Charred and glazed with maple-chili butter, served over roasted fingerlings and wilted black kale. A fall riff on Max Dale’s steak bites.”
        """
        return prompt

    def dish_schema(self):
        schema = {
            "name": "dish_list",
            "type": "json_schema",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "dishes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "suggested_price":{"type":"string"},
                                "ingredient_overlap": {
                                    "type": "array", "items": {"type": "string"}
                                },
                                "category_tags": {
                                    "type": "array", "items": {"type": "string"}
                                },
                            },
                            "required": ["title", "description", "ingredient_overlap", "category_tags","suggested_price"],
                            "additionalProperties": False,
                        },
                        "minItems": 3,
                        "maxItems": 3,
                    }
                },
                "required": ["dishes"],
                "additionalProperties": False,
            },
                }
        return schema

    async def _generate_dishes_for_concept(self, concept):
        concept_payload = self._normalize_concept(concept)

        if not self.openai_client:
            logger.info("OpenAI client not configured; returning no dishes.")
            return []

        response = await self.openai_client.responses.create(
            model="gpt-4.1-mini",
            input=self.dish_prompt(concept_payload)+self.locale_summary,
            text={"format": self.dish_schema()},
        )

        data = json.loads(response.output[0].content[0].text)
        logger.info("Generate dishes OpenAI response: %s", data)

        return data.get("dishes", [])

    async def _generate_image(self, dish) -> str:
        title = dish.get("title", "")
        description = dish.get("description", "")
        overlap = ", ".join(dish.get("ingredient_overlap", []))
        tags = ", ".join(dish.get("category_tags", []))

        image_prompt = f"""
                Create a high-definition, photorealistic food photograph of the dish "{title}".
                Dish description: {description}
                Shared ingredients to highlight: {overlap or "chef's selection of seasonal produce"}.
                Styling cues and tags: {tags or "restaurant special"}.

                Focus tightly on the plated dish, styled on a dark wood or slate surface that matches a classic Pacific Northwest steakhouse.
                Lighting should be soft, directional, and slightly moody—evoking a warm, intimate booth atmosphere.

                Perspective: macro / close-up view, shallow depth of field, natural restaurant light.

                Composition: one plated dish centered or slightly off-center, minimal props (subtle garnish, cutlery, or linen only).

                Color tone: warm neutrals, gentle highlights, no oversaturation.

                Background: blurred and understated; focus remains entirely on the textures of the food.

                Style: lifelike realism, no visible hands or logos, no text overlays.

                Output: single 16:9 HD image suitable for restaurant web and menu use.
        """

        if not self.replicate_client:
            return ""

        # --- Generate image with Replicate ---
        try:
            output = await asyncio.to_thread(
                self.replicate_client.run,
                self.REPLICATE_MODEL,
                input={
                    "prompt": image_prompt,
                    "output_format": "jpg",
                    "output_quality": 100,
                },
            )
        except Exception as exc:
            logger.warning("Replicate generation failed: %s", exc, exc_info=True)
            return ""

        # --- Upload first image URL from Replicate to Cloudinary ---
        try:
            replicate_url = output[0] if isinstance(output, list) else output
            if not replicate_url:
                return ""
            upload_result = await asyncio.to_thread(
                cloudinary.uploader.upload,
                replicate_url,
                folder="dish_images",
                public_id=str(uuid.uuid4()),
                overwrite=True,
                resource_type="image",
            )
            final_url = upload_result.get("secure_url", "")
            logger.info("Dish image uploaded for: %s", final_url)
            return final_url
        except Exception as exc:
            logger.warning("Cloudinary upload failed: %s", exc, exc_info=True)
            return ""
