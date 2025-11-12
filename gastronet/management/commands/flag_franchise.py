import re, os, time
import logging
from django.core.management.base import BaseCommand
from openai import OpenAI
from gastronet.models import Restaurant

logger = logging.getLogger(__name__)

FRANCHISE_PATTERNS = [
    r"mcdonald'?s", r"taco\s?bell", r"jack\s?in\s?the\s?box", r"denn(y'?s)?",
    r"burger\s?king", r"subway", r"domino'?s", r"papa\s?john(?:’s)?",
    r"kfc", r"starbucks", r"wendy('?s)?", r"arbys?", r"sonic", r"five\s?guys",
    r"buffalo\s?wild\s?wings", r"chipotle", r"panera", r"little\s?caesars?",
    r"red\s?robin", r"olive\s?garden", r"applebee('?s)?", r"ihop",
    r"wingstop", r"panda\s?express",
    # Pacific Northwest regional chains:
    r"taco\s*time", r"ivars?", r"salty('?s)?", r"burgerville", r"dick'?s\s?drive[-\s]?in",
    r"mod\s?pizza"
]
FRANCHISE_RE = re.compile("|".join(FRANCHISE_PATTERNS), re.I)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def is_franchise_llm(name, desc):
    prompt = f"""
    Determine if this restaurant appears to be part of a chain/franchise.
    Answer 'true' or 'false' only.
    Name: {name}
    Description: {desc or ""}
    """
    try:
        resp = client.responses.create(model="gpt-4o-mini", input=prompt)
        return "true" in resp.output_text.lower()
    except Exception as exc:
        logger.warning("LLM failure checking franchise for %s: %s", name, exc, exc_info=True)
        return False

class Command(BaseCommand):
    help = "Flag or optionally delete franchise restaurants"

    def add_arguments(self, parser):
        parser.add_argument("--delete", action="store_true",
                            help="Delete flagged franchise records instead of just marking")

    def handle(self, *args, **opts):
        delete = opts["delete"]
        qs = Restaurant.objects.using("gastronet").all()
        flagged = 0

        for r in qs:
            name = r.name or ""
            desc = r.description or ""
            if FRANCHISE_RE.search(name):
                r.is_franchise = True
            else:
                if len(desc) > 20 and is_franchise_llm(name, desc):
                    r.is_franchise = True

            if getattr(r, "is_franchise", False):
                flagged += 1
                if delete:
                    r.delete()
                    self.stdout.write(f"🗑️  Deleted franchise: {name}")
                else:
                    r.save(update_fields=["is_franchise"])
                    self.stdout.write(f"⚠️  Flagged franchise: {name}")

            time.sleep(0.2)

        self.stdout.write(self.style.SUCCESS(f"✅ Done. Flagged or removed {flagged} franchise entries."))
