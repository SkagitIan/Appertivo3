# app/services/content_pipeline.py
import os, json, re, logging
from datetime import date
from typing import Any, Dict, List, Optional, TypedDict

from dotenv import load_dotenv
load_dotenv()

from pydantic import BaseModel, Field, ValidationError
from django.utils.text import slugify

from openai import OpenAI

# ---------- Logging ----------
logger = logging.getLogger(__name__)

# ---------- OpenAI client & model choices ----------
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=3600)
MODEL = "gpt-5-nano-2025-08-07"
DEEP_MODEL = "o4-mini-deep-research"

# ---------- Optional price config ----------
def _price_from_env(key: str, model: str) -> Optional[float]:
    raw = os.getenv(key, "")
    if ":" in raw:
        m, p = raw.split(":", 1)
        if m.strip() == model and re.match(r"^\d+(\.\d+)?$", p.strip()):
            return float(p.strip())
    return None
class CostMeter:
    def __init__(self, model: str):
        self.model = model
        self.input_tokens = 0
        self.output_tokens = 0
        self.in_price = _price_from_env("PRICE_IN_PER_1K", model)
        self.out_price = _price_from_env("PRICE_OUT_PER_1K", model)

    def add(self, usage: Optional[Any]):
        if not usage:
            return
        try:
            # Handles OpenAI ResponseUsage object
            in_tokens = getattr(usage, "input_tokens", None)
            out_tokens = getattr(usage, "output_tokens", None)
            if in_tokens is not None:
                self.input_tokens += int(in_tokens)
            if out_tokens is not None:
                self.output_tokens += int(out_tokens)
        except Exception:
            # Fallback if dict-like
            self.input_tokens += int(usage.get("input_tokens", 0))
            self.output_tokens += int(usage.get("output_tokens", 0))

    def dollars(self) -> Optional[float]:
        """Return total USD cost if PRICE_IN_PER_1K and PRICE_OUT_PER_1K are set in .env"""
        if self.in_price is None or self.out_price is None:
            return None
        return round(
            (self.input_tokens / 1000.0) * self.in_price
            + (self.output_tokens / 1000.0) * self.out_price,
            2,
        )


# ---------- Schemas ----------
class Idea(BaseModel):
    title: str
    angle: str
    audience: str = "restaurateurs"


class IdeaSet(BaseModel):
    ideas: List[Idea] = Field(..., min_items=6, max_items=12)


class ScoredIdea(BaseModel):
    title: str
    score: float = Field(..., ge=0, le=10)
    rationale: str


class Brief(BaseModel):
    working_title: str
    promise: str
    outline: List[str] = Field(..., min_items=5, max_items=10)
    voice_notes: str
    sources_needed: List[str]


class ResearchNote(BaseModel):
    source: str
    key_facts: List[str]
    url: Optional[str] = None


class Draft(BaseModel):
    h1: str
    sections: List[Dict[str, str]]


class SeoMeta(BaseModel):
    slug: str
    meta_title: str = Field(..., max_length=60)
    meta_description: str = Field(..., max_length=155)
    keywords: List[str]
    og_image_hint: str


class PipelineResult(BaseModel):
    picked: ScoredIdea
    brief: Brief
    research: List[ResearchNote]
    draft: Draft
    edited: Draft
    seo: SeoMeta
    html: str

def ask_text(sys: str, user: str, cost: CostMeter) -> str:
    """Ask the model for plain text. Tracks token usage in 'cost'."""
    logger.info("ask_text called with sys='%s...'", sys[:40])
    kwargs: Dict[str, Any] = {
        "model": MODEL,
        "instructions": sys,
        "input": user,
    }
    try:
        resp = client.responses.create(**kwargs)
        cost.add(getattr(resp, "usage", None))
        raw = getattr(resp, "output_text", None) or resp.output[0].content[0].text
        logger.debug("ask_text raw response: %s", raw[:200])
        return raw.strip()
    except Exception as e:
        logger.exception("ask_text failed: %s", e)
        raise


# ---------- Low-ceremony JSON helper ----------
def ask_json(sys: str, user: str, schema_model: Any, cost: CostMeter) -> Any:
    logger.info("ask_json called with sys='%s...'", sys[:40])
    schema = schema_model.model_json_schema() if hasattr(schema_model, "model_json_schema") else None

    kwargs: Dict[str, Any] = {
        "model": MODEL,
        "instructions": sys,
        "input": user,
    }

    if schema:
        kwargs["text"] = {
            "format": {
                "text": "json_schema",
                "json_schema": {"name": schema_model.__name__, "schema": schema, "strict": True},
            }
        }

    try:
        resp = client.responses.create(**kwargs)
        cost.add(getattr(resp, "usage", None))
        raw = getattr(resp, "output_text", None) or resp.output[0].content[0].text
        logger.debug("ask_json raw response: %s", raw[:200])
        data = json.loads(raw)
        return schema_model.model_validate(data) if schema else data

    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning("ask_json validation/JSON error, retrying: %s", e)
        kwargs["temperature"] = 0.7
        resp2 = client.responses.create(**kwargs)
        cost.add(getattr(resp2, "usage", None))
        raw2 = getattr(resp2, "output_text", None) or resp2.output[0].content[0].text
        data2 = json.loads(raw2)
        return schema_model.model_validate(data2) if schema else data2

    except Exception as e:
        logger.exception("ask_json failed: %s", e)
        if schema and "text.format" in str(e):
            kwargs.pop("text", None)
            kwargs["instructions"] = (
                sys + "\nReturn STRICT JSON only, matching this schema:\n" + json.dumps(schema)
            )
            resp3 = client.responses.create(**kwargs)
            cost.add(getattr(resp3, "usage", None))
            raw3 = getattr(resp3, "output_text", None) or resp3.output[0].content[0].text
            data3 = json.loads(raw3)
            return schema_model.model_validate(data3)
        raise

def brainstorm_ideas(topic_hint: str, cost: CostMeter) -> str:
    sys = "You are a senior content strategist for the restaurant industry."
    user = f"Brainstorm 10 article ideas for restaurateurs. Hint: {topic_hint}"
    return ask_text(sys, user, cost)


def score_and_pick(ideas_text: str, cost: CostMeter) -> str:
    sys = "You are an editorial director choosing the strongest angle."
    user = f"Here are the ideas:\n{ideas_text}\nPick the best one and explain why."
    return ask_text(sys, user, cost)


def make_brief(picked_text: str, cost: CostMeter) -> str:
    sys = "You are a professional managing editor writing briefs."
    user = f"Create a brief for this idea:\n{picked_text}"
    return ask_text(sys, user, cost)


def deep_research(topic_hint: str, cost: CostMeter) -> str:
    logger.info("Starting deep research for topic='%s'", topic_hint)
    sys = "You are doing web research for restaurateurs. Return concise notes."
    user = f"Research topic: {topic_hint}"
    kwargs = {
        "model": DEEP_MODEL,
        "instructions": sys,
        "input": user,
        "tools": [{"type": "web_search"}],
        "max_tool_calls": 40,
    }
    resp = client.responses.create(**kwargs)
    cost.add(getattr(resp, "usage", None))
    raw = getattr(resp, "output_text", None) or resp.output[0].content[0].text
    return raw.strip()


def draft_article(brief_text: str, research_text: str, cost: CostMeter) -> str:
    sys = "You are a trade journalist writing for restaurant operators."
    user = f"Brief:\n{brief_text}\nResearch:\n{research_text}"
    return ask_text(sys, user, cost)


def edit_article(draft_text: str, cost: CostMeter) -> str:
    sys = "You are a sharp editor polishing a professional article."
    user = f"Edit this draft:\n{draft_text}"
    return ask_text(sys, user, cost)


def make_seo(edited_text: str, cost: CostMeter) -> str:
    sys = "You are an SEO specialist optimizing professional articles."
    user = f"Generate slug, meta title, description, keywords for this article:\n{edited_text}"
    return ask_text(sys, user, cost)

# ---------- Tailwind formatter ----------
PALETTE = {"purple": "#B993D6", "orange": "#f08000", "green": "#58B09C", "ink": "#49475B", "black": "#14080E"}


def format_tailwind_html(edited_text: str) -> str:
    logger.info("Formatting article into Tailwind HTML")
    return f"""
            <section class="max-w-3xl mx-auto py-10 px-4">
            <header class="mb-8">
                <h1 class="text-3xl md:text-4xl font-extrabold tracking-tight" style="color:{PALETTE['ink']}">
                Article
                </h1>
                <div class="prose prose-slate max-w-none leading-7">
                <p>{edited_text}</p>
                </div>
            </header>
            </section>
            """.strip()



# ---------- Orchestrator ----------
class RunResult(TypedDict):
    pipeline: Dict[str, Any]
    tokens_input: int
    tokens_output: int
    usd_cost: Optional[float]


def run_pipeline(topic_hint: str) -> RunResult:
    logger.info("Running pipeline for topic='%s'", topic_hint)
    cost = CostMeter(MODEL)

    ideas = brainstorm_ideas(topic_hint, cost)
    picked = score_and_pick(ideas, cost)
    brief = make_brief(picked, cost)
    research = deep_research(topic_hint, cost)
    draft = draft_article(brief, research, cost)
    edited = edit_article(draft, cost)
    seo = make_seo(edited, cost)
    html = format_tailwind_html(edited)

    pipeline = {
        "picked": picked,
        "brief": brief,
        "research": research,
        "draft": draft,
        "edited": edited,
        "seo": seo,
        "html": html,
    }

    logger.info(
        "Pipeline complete for topic='%s'. tokens_in=%s, tokens_out=%s, usd_cost=%s",
        topic_hint, cost.input_tokens, cost.output_tokens, cost.dollars()
    )

    return {
        "pipeline": pipeline,
        "tokens_input": cost.input_tokens,
        "tokens_output": cost.output_tokens,
        "usd_cost": cost.dollars(),
    }


def save_article(topic_hint: str):
    from app.models import Article
    result = run_pipeline(topic_hint)
    pr = result["pipeline"]

    a = Article.objects.create(
        status="draft",
        topic_hint=topic_hint,
        title=pr["brief"][:120],  # crude: first line of brief as title
        slug=slugify(pr["picked"].split()[0:5]),  # crude: first 5 words
        html=pr["html"],
        seo_title=pr["seo"][:60],
        seo_description=pr["seo"][:155],
        keywords="",  # you could parse from pr["seo"] if wanted
        pipeline_json=pr,
        tokens_input=result["tokens_input"],
        tokens_output=result["tokens_output"],
        usd_cost=result["usd_cost"],
    )
    return a, result
