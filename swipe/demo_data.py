"""Demo content helpers for the swipe experience."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List
from uuid import uuid4


class DishList(list):
    """List subclass that mimics Django's related manager for templates."""

    def all(self) -> "DishList":
        return self


@dataclass
class DemoDish:
    id: str
    name: str
    reasoning: str
    price: str
    image_url: str
    ingredients: List[str]
    concept_id: str
    is_favorite: bool = False
    is_seen: bool = True

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "reasoning": self.reasoning,
            "price": self.price,
            "image_url": self.image_url,
            "ingredients": list(self.ingredients),
            "concept_id": self.concept_id,
            "is_favorite": self.is_favorite,
            "is_seen": self.is_seen,
        }

    @property
    def display_image_url(self) -> str:
        return self.image_url

    @property
    def variation_endpoint(self) -> str:
        return "#"


@dataclass
class DemoConcept:
    id: str
    name: str
    subtitle: str
    meta_reasoning: str
    meta_ingredients: List[str]
    display_sketch_url: str
    dishes: DishList = field(default_factory=DishList)
    is_favorite: bool = False
    is_seen: bool = True

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "subtitle": self.subtitle,
            "meta_reasoning": self.meta_reasoning,
            "meta_ingredients": list(self.meta_ingredients),
            "display_sketch_url": self.display_sketch_url,
            "is_favorite": self.is_favorite,
            "is_seen": self.is_seen,
            "dishes": [dish.to_dict() for dish in self.dishes],
        }


@dataclass
class DemoState:
    concepts: List[DemoConcept]
    buffers: Dict[str, List[Dict[str, object]]]
    favorite_concepts: List[DemoConcept]
    favorite_dishes: List[DemoDish]
    restaurant_name: str = "Appertivo Demo"

    def as_payload(self) -> Dict[str, object]:
        return {
            "concepts": [concept.to_dict() for concept in self.concepts],
            "buffers": self.buffers,
            "favorites": {
                "concept_ids": [concept.id for concept in self.favorite_concepts],
                "dish_ids": [dish.id for dish in self.favorite_dishes],
            },
        }


def _build_dishes(concept_id: str, specs: Iterable[Dict[str, object]]) -> DishList:
    dishes: DishList = DishList()
    for spec in specs:
        dishes.append(
            DemoDish(
                id=str(spec.get("id", uuid4())),
                name=str(spec["name"]),
                reasoning=str(spec["reasoning"]),
                price=str(spec["price"]),
                image_url=str(spec["image_url"]),
                ingredients=list(spec["ingredients"]),
                concept_id=concept_id,
                is_favorite=bool(spec.get("is_favorite", False)),
                is_seen=bool(spec.get("is_seen", True)),
            )
        )
    return dishes


def build_demo_state() -> DemoState:
    concept_specs = [
        {
            "id": "demo-concept-midnight",
            "name": "Midnight Greenhouse",
            "subtitle": "Nocturnal garden tasting",
            "meta_reasoning": "An urban greenhouse after dark—herbal pairings, shimmering textures, and soft floral aromatics.",
            "meta_ingredients": ["verbena", "charred citrus", "garden herbs"],
            "sketch": "https://images.unsplash.com/photo-1482049016688-2d3e1b311543?auto=format&fit=crop&w=1200&q=80",
            "dishes": [
                {
                    "name": "Cedar Smoked Burrata",
                    "reasoning": "Burrata warmed over cedar smoke with preserved lemon gel and basil oil pearls.",
                    "price": "$18",
                    "image_url": "https://images.unsplash.com/photo-1604908177571-7727ea0e8b5c?auto=format&fit=crop&w=800&q=80",
                    "ingredients": ["basil oil", "cedar smoke", "preserved lemon"],
                    "is_favorite": True,
                },
                {
                    "name": "Glow Garden Tart",
                    "reasoning": "Charred leek tart with whipped goat cheese, luminous cucumber glaze, and pickled grape blossoms.",
                    "price": "$22",
                    "image_url": "https://images.unsplash.com/photo-1540189549336-e6e99c3679fe?auto=format&fit=crop&w=800&q=80",
                    "ingredients": ["goat cheese", "cucumber", "grape blossoms"],
                },
                {
                    "name": "Moonlit Celery Consommé",
                    "reasoning": "Clarified celery broth with chilled melon pearls, verbena oil, and crispy quinoa.",
                    "price": "$16",
                    "image_url": "https://images.unsplash.com/photo-1544510808-91bcbee1df55?auto=format&fit=crop&w=800&q=80",
                    "ingredients": ["verbena", "melon", "quinoa"],
                },
            ],
            "buffer": [
                {
                    "name": "Candlelit Fennel Agnolotti",
                    "reasoning": "Handmade agnolotti with roasted fennel butter, citrus ash, and midnight herb dust.",
                    "price": "$24",
                    "image_url": "https://images.unsplash.com/photo-1540189549336-e6e99c3679fe?auto=format&fit=crop&w=800&q=70",
                    "ingredients": ["fennel", "citrus ash", "herb dust"],
                },
                {
                    "name": "Twilight Herb Pavlova",
                    "reasoning": "Crisp meringue with lavender cremeux, honey gelée, and candied rosemary sprigs.",
                    "price": "$14",
                    "image_url": "https://images.unsplash.com/photo-1504674900247-0877df9cc836?auto=format&fit=crop&w=800&q=80",
                    "ingredients": ["lavender", "honey", "rosemary"],
                },
            ],
        },
        {
            "id": "demo-concept-coastal",
            "name": "Coastal Ember",
            "subtitle": "Fire-kissed shoreline cuisine",
            "meta_reasoning": "A seaside firepit with briny contrasts, ember-kissed seafood, and citrusy brightness.",
            "meta_ingredients": ["yuzu", "coal-charred shellfish", "sea herbs"],
            "sketch": "https://images.unsplash.com/photo-1498654896293-37aacf113fd9?auto=format&fit=crop&w=1200&q=80",
            "dishes": [
                {
                    "name": "Charred Citrus Crudo",
                    "reasoning": "Dayboat snapper cured with smoked salt, charred yuzu, and sea fennel.",
                    "price": "$21",
                    "image_url": "https://images.unsplash.com/photo-1504674900247-0877df9cc836?auto=format&fit=crop&w=800&q=70",
                    "ingredients": ["snapper", "yuzu", "sea fennel"],
                    "is_favorite": True,
                },
                {
                    "name": "Coal-Fired Octopus",
                    "reasoning": "Octopus glazed with smoked paprika butter over ember potatoes and black garlic aioli.",
                    "price": "$28",
                    "image_url": "https://images.unsplash.com/photo-1525755662778-989d0524087e?auto=format&fit=crop&w=800&q=80",
                    "ingredients": ["octopus", "paprika", "black garlic"],
                },
                {
                    "name": "Sea Lettuce Bouillabaisse",
                    "reasoning": "Saffron broth with mussels, charred tomato, and delicate sea lettuce ribbons.",
                    "price": "$26",
                    "image_url": "https://images.unsplash.com/photo-1546069901-ba9599a7e63c?auto=format&fit=crop&w=800&q=80",
                    "ingredients": ["saffron", "mussels", "sea lettuce"],
                },
            ],
            "buffer": [
                {
                    "name": "Embered Scallop Slider",
                    "reasoning": "Seared scallop on grilled milk bread with kelp butter and charred lime relish.",
                    "price": "$17",
                    "image_url": "https://images.unsplash.com/photo-1543353071-873f17a7a088?auto=format&fit=crop&w=800&q=80",
                    "ingredients": ["scallop", "kelp butter", "lime relish"],
                },
                {
                    "name": "Salt Meadow Potatoes",
                    "reasoning": "Smoked potatoes smashed with brown butter, seaweed crumble, and lemon ash.",
                    "price": "$12",
                    "image_url": "https://images.unsplash.com/photo-1504674900247-0877df9cc836?auto=format&fit=crop&w=800&q=60",
                    "ingredients": ["potato", "brown butter", "seaweed"],
                },
            ],
        },
        {
            "id": "demo-concept-velvet",
            "name": "Velvet Hearth",
            "subtitle": "Luxe comfort classics",
            "meta_reasoning": "Supple textures, candlelit warmth, and indulgent classics polished with modern glow.",
            "meta_ingredients": ["black truffle", "brown butter", "aged cheddar"],
            "sketch": "https://images.unsplash.com/photo-1470337458703-46ad1756a187?auto=format&fit=crop&w=1200&q=80",
            "dishes": [
                {
                    "name": "Truffled Brioche Fondue",
                    "reasoning": "Mini brioche loaf filled with molten cheddar, black truffle, and browned thyme butter.",
                    "price": "$19",
                    "image_url": "https://images.unsplash.com/photo-1504674900247-0877df9cc836?auto=format&fit=crop&w=800&q=75",
                    "ingredients": ["brioche", "cheddar", "black truffle"],
                    "is_favorite": True,
                },
                {
                    "name": "Velvet Short Rib",
                    "reasoning": "Slow-braised short rib with smoked sweet potato silk and cacao demi-glace.",
                    "price": "$34",
                    "image_url": "https://images.unsplash.com/photo-1466978913421-dad2ebd01d17?auto=format&fit=crop&w=800&q=80",
                    "ingredients": ["short rib", "sweet potato", "cacao"],
                },
                {
                    "name": "Amber Brûlée Cheesecake",
                    "reasoning": "Brown butter cheesecake with torched maple brûlée and candied cocoa nibs.",
                    "price": "$13",
                    "image_url": "https://images.unsplash.com/photo-1499636136210-6f4ee915583e?auto=format&fit=crop&w=800&q=80",
                    "ingredients": ["maple", "cocoa nib", "cheesecake"],
                },
            ],
            "buffer": [
                {
                    "name": "Fireplace Potato Gratin",
                    "reasoning": "Thin potato layers baked with smoked gouda cream and charred leek crumble.",
                    "price": "$18",
                    "image_url": "https://images.unsplash.com/photo-1506086679521-2bf8d8f3f9ae?auto=format&fit=crop&w=800&q=80",
                    "ingredients": ["potato", "smoked gouda", "leek"],
                },
                {
                    "name": "Nightcap Affogato",
                    "reasoning": "Burnt sugar gelato with espresso caramel pour-over and chocolate smoke.",
                    "price": "$11",
                    "image_url": "https://images.unsplash.com/photo-1504674900247-0877df9cc836?auto=format&fit=crop&w=800&q=65",
                    "ingredients": ["espresso", "burnt sugar", "chocolate"],
                },
            ],
        },
    ]

    concepts: List[DemoConcept] = []
    buffers: Dict[str, List[Dict[str, object]]] = {}
    favorite_concepts: List[DemoConcept] = []
    favorite_dishes: List[DemoDish] = []

    for spec in concept_specs:
        concept_id = str(spec.get("id") or uuid4())
        dishes = _build_dishes(concept_id, spec["dishes"])
        concept = DemoConcept(
            id=concept_id,
            name=spec["name"],
            subtitle=spec["subtitle"],
            meta_reasoning=spec["meta_reasoning"],
            meta_ingredients=list(spec["meta_ingredients"]),
            display_sketch_url=spec["sketch"],
            dishes=dishes,
            is_favorite=any(dish.is_favorite for dish in dishes),
        )
        concepts.append(concept)

        buffers[concept_id] = [
            {
                **dish_dict,
                "concept_id": concept_id,
                "id": str(uuid4()),
                "is_favorite": False,
                "is_seen": False,
            }
            for dish_dict in spec["buffer"]
        ]

        favorite_concepts.append(concept)
        favorite_dishes.extend([dish for dish in dishes if dish.is_favorite])

    return DemoState(
        concepts=concepts,
        buffers=buffers,
        favorite_concepts=favorite_concepts,
        favorite_dishes=favorite_dishes,
    )

