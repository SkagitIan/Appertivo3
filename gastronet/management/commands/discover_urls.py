import os, json, time, requests, logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from django.core.management.base import BaseCommand
from django.utils import timezone
from openai import OpenAI
from gastronet.models import Restaurant, CrawlLog

logger = logging.getLogger(__name__)
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


# -----------------------------------------------------------
# Helpers
# -----------------------------------------------------------

def brave_find_homepage(name, city, api_key):
    """Use Brave API to find a restaurant’s main site."""
    headers = {"Accept": "application/json", "X-Subscription-Token": api_key}
    queries = [
        f"{name} {city} restaurant official site",
        f"{name} {city} restaurant",
        f"{name} restaurant"
    ]
    for q in queries:
        try:
            res = requests.get(BRAVE_URL, params={"q": q, "count": 5, "country": "US"},
                               headers=headers, timeout=15)
            if res.status_code != 200:
                logger.warning("Brave query %s -> %s", q, res.status_code)
                continue
            data = res.json()
            for hit in (data.get("web") or {}).get("results", []):
                url = hit.get("url")
                if not url:
                    continue
                if any(bad in url.lower() for bad in ["yelp", "tripadvisor", "facebook", "ubereats", "grubhub"]):
                    continue
                logger.debug("Brave candidate: %s", url)
                return url
        except Exception as exc:
            logger.warning("Brave query failed for %s: %s", q, exc, exc_info=True)
            continue
    return None


def clean_html_body(url):
    """Fetch homepage HTML and return only the <body> cleaned of scripts/styles."""
    try:
        res = requests.get(url, timeout=12, headers={"User-Agent": "GastroNetBot/1.0"})
        soup = BeautifulSoup(res.text, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        body = soup.body or soup
        html = body.prettify()
        logger.debug("Fetched and cleaned body from %s (%d chars)", url, len(html))
        return html
    except Exception as exc:
        logger.warning("Failed to fetch/clean HTML from %s: %s", url, exc)
        return ""


def find_menu_urls_llm(html_body, base_url, openai_key):
    """Ask small LLM to find food/drink menu URLs in homepage HTML."""
    client = OpenAI(api_key=openai_key)
    prompt = f"""
    You are analyzing HTML code from a restaurant homepage.
    Find URLs that likely point to the restaurant's FOOD or DRINK menus.
    These may be on this domain or external ordering platforms.
    Do NOT return navigation, privacy policy, careers, or non-food links.
    also look in the anchor text, you might see website.com/page but the text is 'Menu'
    Base URL: {base_url}

    Return ONLY a JSON list of absolute URLs.
    Example:
    ["https://example.com/menu", "https://toasttab.com/example/menu"]

    ---
    {html_body[:8000]}
    """
    try:
        logger.info("Submitting HTML prompt to OpenAI for %s (chars=%d)", base_url, len(prompt))
        resp = client.responses.create(model="gpt-4o-mini", input=prompt)
        raw_output = getattr(resp, "output_text", "") or ""
        logger.info("OpenAI raw output for %s: %s", base_url, raw_output[:600].strip())
        if not raw_output:
            # Fallback: try to read from first choice if SDK structure changes.
            first_content = (
                resp.output[0].content[0].text
                if getattr(resp, "output", None)
                else ""
            )
            raw_output = first_content or ""
            logger.debug("Fallback raw output for %s: %s", base_url, raw_output[:600].strip())
        data = json.loads(raw_output)
        urls = [u for u in data if isinstance(u, str) and u.startswith("http")]
        logger.debug("LLM discovered %d candidate URLs", len(urls))
        return urls
    except Exception as exc:
        logger.warning("LLM menu discovery failed for %s: %s", base_url, exc, exc_info=True)
        return []


def verify_url_live(url):
    """HEAD-check a URL for 200/OK."""
    try:
        r = requests.head(url, allow_redirects=True, timeout=8)
        ok = 200 <= r.status_code < 400
        logger.debug("HEAD %s -> %s", url, r.status_code)
        return ok
    except Exception as exc:
        logger.debug("HEAD failed for %s: %s", url, exc)
        return False


# -----------------------------------------------------------
# Command
# -----------------------------------------------------------

class Command(BaseCommand):
    help = "Discover restaurant websites and menu URLs via Brave + LLM (HTML body parsing)."

    def add_arguments(self, parser):
        parser.add_argument("--batch", type=int, default=100)
        parser.add_argument("--city", type=str, default="Seattle")

    def handle(self, *args, **opts):
        batch = opts["batch"]
        city = opts["city"]

        brave_key = os.getenv("BRAVE_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")
        if not brave_key or not openai_key:
            self.stderr.write("Missing BRAVE_API_KEY or OPENAI_API_KEY.")
            return

        log = CrawlLog.objects.using("gastronet").create(task="discover_urls_llmhtml", scope=city)
        logger.info("Starting discovery batch=%s city=%s", batch, city)

        qs = Restaurant.objects.using("gastronet").filter(website__isnull=True).order_by("id")[:batch]

        for r in qs:
            try:
                logger.info("Processing restaurant id=%s name=%s", r.id, r.name)

                # 1️⃣ Find homepage
                site = brave_find_homepage(r.name, city, brave_key)
                if not site:
                    log.skip_count += 1
                    logger.info("No homepage found for %s", r.name)
                    continue
                logger.info("Homepage candidate for %s resolved via Brave: %s", r.name, site)
                r.website = site
                r.url_source = "brave"
                self.stdout.write(f"🌐 {r.name}: {site}")

                # 2️⃣ Fetch & clean HTML
                html_body = clean_html_body(site)
                if not html_body:
                    log.skip_count += 1
                    logger.info("HTML body empty for %s, skipping.", r.name)
                    continue
                logger.info("Fetched HTML body for %s (%d chars).", r.name, len(html_body))

                # 3️⃣ Ask LLM for menu URLs
                candidates = find_menu_urls_llm(html_body, site, openai_key)
                candidates = [urljoin(site, u) if u.startswith("/") else u for u in candidates]
                candidates = [u for u in candidates if any(k in u.lower() for k in ["menu", "order", "food"])]
                logger.debug("Candidate count for %s: %d", r.name, len(candidates))
                if not candidates:
                    logger.info("LLM returned no usable menu candidates for %s.", r.name)

                # 4️⃣ Verify candidates
                found = None
                for url in candidates:
                    logger.info("Verifying candidate for %s: %s", r.name, url)
                    if verify_url_live(url):
                        found = url
                        logger.info("Verified menu URL for %s: %s", r.name, url)
                        break

                # 5️⃣ Save
                r.url_checked_at = timezone.now()
                if found:
                    r.menu_url = found
                    r.url_source = "llm_htmlbody"
                    log.success_count += 1
                    self.stdout.write(f"🍽️  Menu found: {found}")
                else:
                    log.skip_count += 1
                    self.stdout.write(f"⚠️  No menu found for {r.name}")

                r.save(using="gastronet", update_fields=["website","menu_url","url_source","url_checked_at"])
                time.sleep(0.3)

            except Exception as exc:
                log.error_count += 1
                logger.exception("Error processing %s: %s", r.name, exc)
                self.stderr.write(f"Error {r.name}: {exc}")
                continue

        log.ended_at = timezone.now()
        log.save(update_fields=["success_count","skip_count","error_count","ended_at"])
        logger.info("Discovery done success=%s skip=%s error=%s",
                    log.success_count, log.skip_count, log.error_count)
        self.stdout.write(self.style.SUCCESS(
            f"✅ Discovery complete. Found={log.success_count}, Skipped={log.skip_count}, Errors={log.error_count}"
        ))






