from celery import shared_task
from django.utils import timezone

from gastronet.management.commands.seed_places import Command as SeedPlacesCommand
from gastronet.management.commands.fetch_reviews import Command as FetchReviewsCommand
from gastronet.management.commands.schedule_refresh import Command as ScheduleRefreshCommand
from gastronet.models import CrawlLog


@shared_task(bind=True, max_retries=3)
def seed_places(self, query="restaurants in Seattle, WA", limit=500):
    SeedPlacesCommand().handle(query=query, limit=limit)
    return "seed complete"


@shared_task(bind=True, max_retries=3)
def fetch_reviews(self, batch=60, per_place_limit=10):
    FetchReviewsCommand().handle(batch=batch, per_place_limit=per_place_limit)
    return "reviews fetched"


@shared_task(bind=True, max_retries=3)
def schedule_refresh(self, min_days=3.0, max_days=90.0, alpha=0.8, limit=5000):
    ScheduleRefreshCommand().handle(
        min_days=min_days, max_days=max_days, alpha=alpha, limit=limit
    )
    return "refresh schedule updated"


@shared_task(bind=True)
def heartbeat(self):
    CrawlLog.objects.create(
        task="heartbeat",
        scope="system",
        success_count=1,
        notes=f"beat @ {timezone.now().isoformat()}",
    )
    return "alive"


@shared_task(bind=True, queue="render", max_retries=2)
def render_menu_page(self, url):
    """
    Run Playwright headless render to return HTML of a page.
    Heavy task → runs only on 'render' queue.
    """
    from playwright.async_api import async_playwright
    async def _render():
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url, timeout=25000)
                await asyncio.sleep(2.5)
                html = await page.content()
                await browser.close()
                return html
        except Exception as e:
            return ""

    try:
        return asyncio.run(_render())
    except Exception:
        return ""