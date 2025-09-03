"""Content generation pipeline utilities."""
import os, json, re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from dotenv import load_dotenv
load_dotenv()

from pydantic import BaseModel, Field, ValidationError
from django.utils.text import slugify

from openai import OpenAI
from dataclasses import dataclass


@dataclass
class ContentPipeline:
    """Legacy helper methods for pipeline view."""

    def brainstorm_ideas(self) -> List[str]:
        return ["Idea 1", "Idea 2", "Idea 3"]

    def score_and_pick(self, ideas: List[str]) -> List[Tuple[str, float]]:
        return [(idea, 1.0 - idx * 0.1) for idx, idea in enumerate(ideas)]

    def make_brief(self, idea: str) -> str:
        return f"Brief for {idea}"

    def draft_article(self, research: Dict[str, List[str]]) -> str:
        sources = ", ".join(research.get("sources", []))
        return f"Draft using sources: {sources}"

    def edit_article(self, draft: str) -> str:
        return draft + "\n\nEdited for clarity."

    def make_seo(self, article: str) -> Dict[str, str]:
        return {"meta_title": article[:50], "meta_description": article[:150]}

    def format_article(self, article: str) -> str:
        return f"<p>{article}</p>"


# ---------- OpenAI client & model choices ----------
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", "sk-test"), timeout=3600)
MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano-2025-08-07")               # main writer/editor
DEEP_MODEL = os.getenv("OPENAI_DEEP_MODEL", "o4-mini-deep-research")     # or "o3-deep-research"

# ---------- Optional price config (set in .env if you want $) ----------
# PRICE_IN_PER_1K=gpt-5-nano-2025-08-07:0.15
# PRICE_OUT_PER_1K=gpt-5-nano-2025-08-07:0.60
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

    def add(self, usage: Optional[Dict[str, Any]]):
        if not usage:
            return
        self.input_tokens += int(usage.get("input_tokens", 0))
        self.output_tokens += int(usage.get("output_tokens", 0))

    def dollars(self) -> Optional[float]:
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
    sections: List[Dict[str, str]]  # [{"h2": "...", "body": "..."}]

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
    html: str  # Tailwind formatted

# ---------- Low-ceremony JSON helper ----------
def ask_json(sys: str, user: str, schema_model: Any, cost: CostMeter) -> Any:
    """
    Responses API call that asks for strict JSON matching 'schema_model'.
    Tracks token usage in 'cost'.
    """
    schema = schema_model.model_json_schema() if hasattr(schema_model, "model_json_schema") else None

    kwargs: Dict[str, Any] = {
        "model": MODEL,
        "instructions": sys,   # Responses API: 'instructions' for system/developer
        "input": user,         # string input is fine
    }

    if schema:
        kwargs["text"] = {
            "format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_model.__name__,
                    "schema": schema,
                    "strict": True
                }
            }
        }

    try:
        resp = client.responses.create(**kwargs)
        cost.add(getattr(resp, "usage", None))
        raw = getattr(resp, "output_text", None) or resp.output[0].content[0].text
        data = json.loads(raw)
        return schema_model.model_validate(data) if schema else data

    except (json.JSONDecodeError, ValidationError):
        # one retry with slightly higher temperature
        kwargs["temperature"] = 0.7
        resp2 = client.responses.create(**kwargs)
        cost.add(getattr(resp2, "usage", None))
        raw2 = getattr(resp2, "output_text", None) or resp2.output[0].content[0].text
        data2 = json.loads(raw2)
        return schema_model.model_validate(data2) if schema else data2

    except Exception as e:
        # If your SDK/model doesn’t support structured outputs on this route,
        # drop the 'text' block and ask the model to return JSON by instruction.
        if schema and "text.format" in str(e):
            kwargs.pop("text", None)
            kwargs["instructions"] = (sys + "\nReturn STRICT JSON only, matching this schema:\n"
                                      + json.dumps(schema))
            resp3 = client.responses.create(**kwargs)
            cost.add(getattr(resp3, "usage", None))
            raw3 = getattr(resp3, "output_text", None) or resp3.output[0].content[0].text
            data3 = json.loads(raw3)
            return schema_model.model_validate(data3)
        raise

# ---------- Deep Research via Responses API ----------
def deep_research(topic_hint: str, cost: CostMeter) -> List[ResearchNote]:
    """
    Runs Deep Research using a deep-research model + web search tool.
    Returns List[ResearchNote] validated by Pydantic.
    """
    schema = {
        "type": "array",
        "minItems": 4,
        "maxItems": 8,
        "items": {
            "type": "object",
            "required": ["source", "key_facts"],
            "additionalProperties": False,
            "properties": {
                "source": {"type": "string"},
                "url": {"type": "string"},
                "key_facts": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 6,
                    "items": {"type": "string"},
                },
            },
        },
    }

    instructions = (
        "You are doing web research for restaurateurs. Find recent, citable facts from high-quality sources "
        "(official docs, trade orgs, .gov, primary data). Return strict JSON only matching the schema. "
        "Each note must include 3–6 concise facts. Prefer official docs for Google Business Profile, email, POS, etc."
    )

    user = (
        f"Research topic: {topic_hint}\n"
        "Output: JSON array of notes. Each note has {source, url, key_facts[]}."
    )

    kwargs = {
        "model": DEEP_MODEL,                # e.g. "o4-mini-deep-research" or "o3-deep-research"
        "instructions": instructions,
        "input": user,
        "tools": [
            {"type": "web_search"},  # required for deep-research models
            # {"type": "code_interpreter", "container": {"type": "auto"}},
        ],
        "max_tool_calls": 40,
        "text": {
        "format": {
            "type": "json_schema",
            "json_schema": {
                "name": "ResearchNotes",
                "schema": schema,
                "strict": True
            }
        }
    },
    }

    try:
        resp = client.responses.create(**kwargs)

    except Exception as e:
        # Fallback if this SDK/model build rejects structured outputs under 'text'
        if "text.format" in str(e):
            kwargs.pop("text", None)
            kwargs["instructions"] = (instructions + "\nReturn STRICT JSON only, matching this schema:\n"
                                      + json.dumps(schema))
            resp = client.responses.create(**kwargs)
        else:
            raise

    cost.add(getattr(resp, "usage", None))
    raw = getattr(resp, "output_text", None) or resp.output[0].content[0].text
    data = json.loads(raw)
    return [ResearchNote.model_validate(n) for n in data]

# ---------- (1-7) Core pipeline steps ----------
def brainstorm_ideas(topic_hint: str, cost: CostMeter) -> IdeaSet:
    sys = "You are a senior content strategist for a restaurant-tech blog."
    user = (
        "Brainstorm 10 article ideas for restaurateurs. Mix how-tos, playbooks, and cultural analysis. "
        f"Use this hint: {topic_hint}. Each idea needs a title and angle."
    )
    return ask_json(sys, user, IdeaSet, cost)

def score_and_pick(ideas: IdeaSet, cost: CostMeter) -> ScoredIdea:
    sys = "You evaluate topics by relevance to restaurateurs, novelty, and 90-day search demand."
    bullets = "\n".join([f"- {i.title}: {i.angle}" for i in ideas.ideas])
    user = (
        "Score each idea 0-10 and pick the best one for impact. "
        "Return only the winner with score and rationale.\nIdeas:\n" + bullets
    )
    return ask_json(sys, user, ScoredIdea, cost)

def make_brief(picked: ScoredIdea, prior_notes: List[ResearchNote], cost: CostMeter) -> Brief:
    sys = "You write tight article briefs for senior writers."
    user = (
        f"Create a brief for the idea '{picked.title}'. Promise must be specific and valuable to restaurateurs. "
        "Outline should be 5-8 sections. Include 3-6 sources needed and voice notes (tone, POV). "
        f"Consider these preliminary notes:\n{json.dumps([n.model_dump() for n in prior_notes])}"
    )
    return ask_json(sys, user, Brief, cost)

def draft_article(brief: Brief, research: List[ResearchNote], cost: CostMeter) -> Draft:
    sys = "Expert writer for restaurateurs. Clear, example-rich, non-sales tone. ~1200 words."
    user = (
        f"Write the article with an H1 and several H2 sections.\n"
        f"Brief:\n{brief.model_dump_json()}\n"
        f"Research:\n{json.dumps([r.model_dump() for r in research])}"
    )
    return ask_json(sys, user, Draft, cost)

def edit_article(draft: Draft, cost: CostMeter) -> Draft:
    sys = "Sharp editor. Improve clarity, tighten sentences, add specificity, keep structure, preserve voice."
    user = f"Edit this draft for flow and specificity.\nDraft:\n{draft.model_dump_json()}"
    return ask_json(sys, user, Draft, cost)

def make_seo(edited: Draft, cost: CostMeter) -> SeoMeta:
    sys = "SEO editor for B2B content."
    user = (
        "Create slug, meta title (<=60), meta description (<=155), 6-10 keywords, and an OG image hint. "
        f"Base it on this edited draft:\n{edited.model_dump_json()}"
    )
    data = ask_json(sys, user, SeoMeta, cost)
    data.slug = slugify(data.slug)[:80]
    return data

# ---------- (8) Tailwind formatter (Appertivo palette) ----------
PALETTE = {
    "purple": "#B993D6",
    "orange": "#f08000",
    "green":  "#58B09C",
    "ink":    "#49475B",
    "black":  "#14080E",
}

def format_tailwind_html(edited: Draft) -> str:
    parts = []
    parts.append(f"""
<section class="max-w-3xl mx-auto py-10 px-4">
  <header class="mb-8">
    <h1 class="text-3xl md:text-4xl font-extrabold tracking-tight" style="color:{PALETTE['ink']}">{edited.h1}</h1>
    <div class="mt-3 h-1.5 w-24 rounded-full" style="background:linear-gradient(90deg,{PALETTE['orange']}, {PALETTE['purple']});"></div>
  </header>
""")
    for sec in edited.sections:
        h2 = sec.get("h2","").strip()
        body = sec.get("body","").strip()
        if not h2 and not body:
            continue
        parts.append(f"""
  <article class="mb-8 p-6 rounded-2xl shadow-sm bg-white border border-slate-100">
    <h2 class="text-xl md:text-2xl font-semibold mb-3" style="color:{PALETTE['black']}">{h2}</h2>
    <div class="prose prose-slate max-w-none leading-7">
      <p class="text-slate-700">{body}</p>
    </div>
  </article>
""")
    parts.append(f"""
  <footer class="mt-10">
    <a href="/resources" class="inline-flex items-center gap-2 px-5 py-3 rounded-xl text-white font-semibold"
       style="background:{PALETTE['orange']}; box-shadow:0 6px 24px rgba(240,128,0,.25)">
      Explore more guides
      <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none"><path d="M9 5l7 7-7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </a>
  </footer>
</section>
""")
    return "\n".join(parts)

# ---------- Orchestrator ----------
class RunResult(TypedDict):
    pipeline: Dict[str, Any]
    tokens_input: int
    tokens_output: int
    usd_cost: Optional[float]

def run_pipeline(topic_hint: str) -> RunResult:
    cost = CostMeter(MODEL)

    # 0) Deep Research (seed notes early)
    prelim_notes = deep_research(topic_hint, cost)
    if not prelim_notes:
        prelim_notes = []

    # 1..7
    ideas = brainstorm_ideas(topic_hint, cost)
    picked = score_and_pick(ideas, cost)
    brief = make_brief(picked, prelim_notes, cost)

    # Optionally add a second pass here using brief.working_title
    research = prelim_notes

    draft = draft_article(brief, research, cost)
    edited = edit_article(draft, cost)
    seo = make_seo(edited, cost)

    # 8) Tailwind HTML
    html = format_tailwind_html(edited)

    pipeline = PipelineResult(
        picked=picked, brief=brief, research=research, draft=draft, edited=edited, seo=seo, html=html
    ).model_dump()

    return {
        "pipeline": pipeline,
        "tokens_input": cost.input_tokens,
        "tokens_output": cost.output_tokens,
        "usd_cost": cost.dollars(),
    }

# ---------- Save helper (Django) ----------
def save_article(topic_hint: str):
    """
    Runs the pipeline and saves to DB. Returns the Article instance and the cost dict.
    """
    from .models import Article, ArticleRevision  # avoid circular import

    result = run_pipeline(topic_hint)
    pr = result["pipeline"]

    html = pr["html"]
    working_title = pr["brief"]["working_title"]
    slug = pr["seo"]["slug"] or slugify(working_title)

    a = Article.objects.create(
        title=working_title,
        slug=slug,
        meta_title=pr["seo"]["meta_title"],
        meta_description=pr["seo"]["meta_description"],
    )
    ArticleRevision.objects.create(
        article=a,
        step="formatted",
        content_md=html,
    )
    return a, result
