import asyncio
import json
import logging
import os
import re
import random
import time
import hashlib
import psutil
from decimal import Decimal
from urllib.parse import urljoin
from datetime import datetime
from dotenv import load_dotenv

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from asgiref.sync import sync_to_async

from pydantic import BaseModel, Field
from typing import List, Optional

from bs4 import BeautifulSoup
from gastronet.models import Restaurant, MenuItem, CrawlLog

# Crawl4AI imports
from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
    LLMConfig,
    LLMExtractionStrategy,
)

load_dotenv()
logger = logging.getLogger(__name__)

# ===========================
# Pydantic schema
# ===========================
class ExtractedMenuItem(BaseModel):
    name: str = Field(..., description="Short food menu item name, no emojis")
    description: Optional[str] = Field(default=None, description="Short menu item description")
    price_text: Optional[str] = Field(default=None, description="Price string (e.g., '$12')")
    section: Optional[str] = Field(default=None, description="Menu category if obvious")
    dietary_tags: List[str] = Field(default_factory=list, description="['vegan','gluten-free',...]")

class MenuSchema(BaseModel):
    items: List[ExtractedMenuItem]


# ===========================
# Helpers
# ===========================
MENU_KEYWORDS = [
    "menu", "menus", "dinner", "lunch", "brunch", "breakfast",
    "takeout", "order", "pickup", "food", "carta", "drinks",
    "beverage", "wine", "beer", "cocktail", "kids"
]
PDF_EXTS = (".pdf",)
PRICE_GRAB = re.compile(r"(\$?\s*\d+(?:[\.,]\d{2})?)")

def _maybe_money_to_decimal(s: Optional[str]) -> Optional[Decimal]:
    if not s:
        return None
    m = PRICE_GRAB.search(s)
    if not m:
        return None
    val = m.group(1).replace("$", "").replace(",", "").strip()
    try:
        return Decimal(val)
    except Exception:
        return None

def make_abs(base: str, link: str) -> str:
    try:
        return urljoin(base, link)
    except Exception:
        return link

def looks_like_menu_link(link_text: str, href: str) -> bool:
    ltext = (link_text or "").lower()
    lhref = (href or "").lower()
    def hit(s: str) -> bool:
        return any(k in s for k in MENU_KEYWORDS)
    return hit(ltext) or hit(lhref) or lhref.endswith(PDF_EXTS)


# ===========================
# Elon-Level Reinforcements
# ===========================
async def run_with_retry(coro_fn, retries=3, delay=3, backoff=2):
    for i in range(retries):
        try:
            return await coro_fn()
        except Exception as e:
            if i == retries - 1:
                raise
            wait = delay * (backoff ** i) + random.uniform(0, 1)
            logger.warning(f"Retry {i+1}/{retries} after {wait:.1f}s due to {e}")
            await asyncio.sleep(wait)

async def throttle_if_overloaded(max_cpu=85, max_mem=85):
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory().percent
    if cpu > max_cpu or mem > max_mem:
        sleep_time = 5 + (cpu - max_cpu) * 0.1
        logger.warning(f"System under load (CPU={cpu}%, MEM={mem}%). Pausing {sleep_time:.1f}s")
        await asyncio.sleep(sleep_time)

@sync_to_async
def record_menu_snapshot(restaurant, url, html, json_data=None, method="plain"):
    from gastronet.models import MenuSnapshot
    h = hashlib.sha256((html or "").encode("utf-8")).hexdigest()
    return MenuSnapshot.objects.create(
        restaurant=restaurant,
        source_url=url,
        text=(html or "")[:10000],
        hash=h,
        parsed_json=json_data or {},
        render_method=method,
    )

@sync_to_async
def create_menu_attempt(restaurant, url, source="heuristic", status=None):
    from gastronet.models import MenuAttempt
    return MenuAttempt.objects.create(
        restaurant=restaurant,
        tried_url=url,
        source=source,
        status=status or "started",
    )

@sync_to_async
def update_crawl_log(log_obj, success=False, skipped=False, errored=False, api_calls=0, cost=0.0):
    if success:
        log_obj.success_count += 1
    if skipped:
        log_obj.skip_count += 1
    if errored:
        log_obj.error_count += 1
    log_obj.api_calls += api_calls
    log_obj.est_cost_usd += cost
    log_obj.save(update_fields=["success_count","skip_count","error_count","api_calls","est_cost_usd"])

def estimate_cost(data, model="gpt-4o-mini"):
    raw = json.dumps(data or {})
    tokens = len(raw) / 4
    pricing = {"gpt-4o-mini": 0.00015, "deepseek-chat": 0.0001}
    rate = pricing.get(model, 0.0002)
    return (tokens / 1000) * rate

from datetime import timedelta

@sync_to_async
def should_reextract(restaurant, days=90):
    """Skip restaurants whose latest menu snapshot is newer than N days."""
    from gastronet.models import MenuSnapshot
    cutoff = timezone.now() - timedelta(days=days)
    latest = (
        MenuSnapshot.objects.filter(restaurant=restaurant)
        .order_by("-fetched_at")
        .first()
    )
    if not latest:
        return True  # no snapshot yet
    if latest.fetched_at < cutoff:
        return True  # older than 90 days
    logger.info(
        f"Skipping {restaurant.name}: recent snapshot from {latest.fetched_at:%Y-%m-%d}"
    )
    return False

# ===========================
# Crawl4AI Config Builders
# ===========================
def build_menu_llm_config(model_name: str = "gpt-4o-mini") -> LLMConfig:
    provider = f"openai/{model_name}"
    return LLMConfig(provider=provider, api_token=os.getenv("OPENAI_API_KEY"))

def build_menu_extraction_config(model_name: str = "gpt-4o-mini") -> CrawlerRunConfig:
    llm_strategy = LLMExtractionStrategy(
        llm_config=build_menu_llm_config(model_name=model_name),
        schema=MenuSchema.model_json_schema(),
        extraction_type="schema",
        input_format="html",
        instruction=(
            "Extract restaurant menu items with JSON schema:\n"
            "name, description, price_text, section, dietary_tags. "
            "Return {'items':[...]} — omit emojis and ads."
        ),
        temperature=0.0,
        chunk_token_threshold=2000,
        overlap_rate=0.15,
        apply_chunking=True,
        verbose=False,
    )
    return CrawlerRunConfig(
        extraction_strategy=llm_strategy,
        cache_mode=CacheMode.BYPASS,
        scan_full_page=True,
        max_scroll_steps=25,
        page_timeout=60000,
        wait_for="css:body",
        stream=False,
    )


# ===========================
# Core Crawl Job
# ===========================
async def process_restaurant(
    crawler: AsyncWebCrawler,
    restaurant: Restaurant,
    model_name: str,
    limit_pages: int,
    skip_discovery: bool = False
) -> int:
    website = getattr(restaurant, "website", None) or getattr(restaurant, "website_url", None)
    if not website:
        logger.info("Skipping %s; no website", restaurant.name)
        return 0

    crawl_log = await sync_to_async(CrawlLog.objects.create)(task="extract_menus", scope=restaurant.name)
    total_saved = 0
    candidate_urls = [website]

    # --- Discovery ---
    if not skip_discovery:
        try:
            logger.info(f"Discovering links for {website}")
            discovery_res = await run_with_retry(lambda: crawler.arun(url=website))
            if discovery_res.success:
                for link in discovery_res.links.get("internal", []):
                    href, text = link.get("href", ""), link.get("text", "")
                    if looks_like_menu_link(text, href):
                        abs_url = make_abs(website, href)
                        if abs_url not in candidate_urls:
                            candidate_urls.append(abs_url)
                            logger.info(f"Found menu link: {abs_url}")
            else:
                logger.warning(f"Discovery failed for {website}: {discovery_res.error_message}")
        except Exception as e:
            logger.exception(f"Discovery exception for {website}: {e}")

    candidate_urls = list(dict.fromkeys(candidate_urls))[:limit_pages]
    logger.info(f"{len(candidate_urls)} candidate pages queued for {restaurant.name}")

    # --- Extraction ---
    menu_cfg = build_menu_extraction_config(model_name)
    logger.info(f"Starting extraction for {restaurant.name} ({model_name})")

    results = await run_with_retry(lambda: crawler.arun_many(urls=candidate_urls, config=menu_cfg))

    for res in results:
        attempt = await create_menu_attempt(restaurant, res.url, source="follow_link")
        if not res.success:
            await sync_to_async(attempt.finish)(
                found=False, parsed=False, status=res.error_message or "crawl_failed"
            )
            await update_crawl_log(crawl_log, errored=True, api_calls=1)
            continue

        try:
            html_content = getattr(res, "html_content", None)
            # --- HTML cleanup ---
            if html_content:
                soup = BeautifulSoup(html_content, "html.parser")
                for tag in soup(["script", "style", "footer", "header", "nav", "form"]):
                    tag.decompose()
                section = soup.find(
                    lambda t: t.name in ["div","section"]
                    and ("menu" in (t.get("id") or "").lower()
                         or any("menu" in c.lower() for c in t.get("class", [])))
                )
                filtered_html = str(section or soup.body or html_content)
                html_content = filtered_html

            # --- Parse JSON ---
            data = json.loads(res.extracted_content or "{}")
            items = data if isinstance(data, list) else data.get("items", [])
            if not items:
                await sync_to_async(attempt.finish)(found=True, parsed=False, status="no_items")
                await update_crawl_log(crawl_log, skipped=True, api_calls=1)
                continue

            cost = estimate_cost(data, model_name)
            saved = await save_menu_items(restaurant, res.url, items)
            await record_menu_snapshot(restaurant, res.url, html_content, json_data=data)
            total_saved += saved

            await sync_to_async(attempt.finish)(found=True, parsed=True, status="success")
            await update_crawl_log(crawl_log, success=True, api_calls=1, cost=cost)

        except Exception as e:
            await sync_to_async(attempt.finish)(found=True, parsed=False, status=str(e))
            await update_crawl_log(crawl_log, errored=True, api_calls=1)
            logger.exception(f"Processing error for {res.url}: {e}")

    crawl_log.ended_at = timezone.now()
    await sync_to_async(crawl_log.save)(update_fields=["ended_at"])
    logger.info(f"✓ Completed {restaurant.name} | {total_saved} items saved")
    return total_saved


# ===========================
# Save Items
# ===========================
@sync_to_async
def save_menu_items(restaurant, url, items):
    logger.info(f"Saving {len(items)} items from {url} for {restaurant.name}")
    saved = 0
    with transaction.atomic():
        for it in items:
            name = (it.get("name") or "").strip()
            if not name:
                continue
            price_dec = _maybe_money_to_decimal(it.get("price_text"))
            MenuItem.objects.update_or_create(
                restaurant=restaurant,
                source_url=url,
                name=name[:255],
                defaults={
                    "description": (it.get("description") or "").strip()[:1000],
                    "price": price_dec,
                    "section": (it.get("section") or "").strip()[:255],
                    "dietary_tags": it.get("dietary_tags") or [],
                    "currency": "USD",
                },
            )
            saved += 1
    return saved


# ===========================
# Runner
# ===========================
async def runner(qs, model_name: str, limit_pages: int, headless: bool, skip_discovery: bool):
    browser_cfg = BrowserConfig(headless=headless, verbose=False)
    total = 0
    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        for restaurant in qs:
            await throttle_if_overloaded()
            # ✅ skip if menu recently fetched
            if not await should_reextract(restaurant, days=90):
                logger.info(f"Skipping {restaurant.name}: snapshot recent.")
                continue
            try:
                total += await process_restaurant(
                    crawler=crawler,
                    restaurant=restaurant,
                    model_name=model_name,
                    limit_pages=limit_pages,
                    skip_discovery=skip_discovery,
                )
            except Exception as e:
                logger.exception(f"Restaurant failed: {restaurant.name} ({e})")
    return total


# ===========================
# Django Command
# ===========================
class Command(BaseCommand):
    help = "Extract menu items using Crawl4AI + LLM with reinforced logging & cost tracking"

    def add_arguments(self, parser):
        parser.add_argument("--model", type=str, default="deepseek-chat")
        parser.add_argument("--limit", type=int, default=5)
        parser.add_argument("--headless", action="store_true", default=True)
        parser.add_argument("--only", type=str, default=None)
        parser.add_argument("--max", type=int, default=200)
        parser.add_argument("--skip-discovery", action="store_true")

    def handle(self, *args, **opts):
        model_name = opts["model"]
        limit_pages = max(1, int(opts["limit"]))
        headless = bool(opts["headless"])
        name_filter = opts["only"]
        max_count = int(opts["max"])
        skip_discovery = opts["skip_discovery"]

        qs = Restaurant.objects.all().order_by("id")
        if name_filter:
            qs = qs.filter(name__icontains=name_filter)
        qs = qs[:max_count]
        restaurants = list(qs)

        self.stdout.write(self.style.NOTICE(f"Processing {len(restaurants)} restaurants..."))
        start = time.time()

        saved = asyncio.run(
            runner(restaurants, model_name, limit_pages, headless, skip_discovery)
        )

        elapsed = time.time() - start
        self.stdout.write(
            self.style.SUCCESS(f"✓ Done. {saved} items saved in {elapsed:.1f}s total.")
        )
        logger.info(f"Extraction complete: {saved} items, {elapsed:.1f}s elapsed")
