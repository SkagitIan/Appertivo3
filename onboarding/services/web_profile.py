"""Utilities for building a lightweight web profile snapshot."""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Iterable, List
from urllib.parse import urljoin, urlparse

import requests

from app import models

logger = logging.getLogger(__name__)
_MAX_PAGES = 6
_TIMEOUT_SECONDS = 10


@dataclass
class PageSnapshot:
    """Lightweight snapshot of a crawled page."""

    url: str
    text: str
    links: List[str]


class _SimpleHTMLParser(HTMLParser):
    """Collect text and anchor links from HTML content."""

    def __init__(self) -> None:
        super().__init__()
        self.text_parts: List[str] = []
        self.links: List[str] = []

    def handle_data(self, data: str) -> None:  # pragma: no cover - html parsing is straightforward
        cleaned = (data or "").strip()
        if cleaned:
            self.text_parts.append(cleaned)

    def handle_starttag(self, tag: str, attrs):  # pragma: no cover - html parsing is straightforward
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.links.append(value)


def _log_profile_call(metadata: dict) -> None:
    """Record a profile crawl for observability."""

    try:
        models.LlmCallLog.objects.create(
            provider="web_profile",
            model_name="crawler",
            call_type=models.LlmCallLog.CallType.TEXT,
            step="onboarding",
            function_name="build_profile",
            metadata=metadata,
        )
    except Exception:  # pragma: no cover - best effort logging
        logger.exception("Failed to record web profile crawl", exc_info=True)


def _normalize_domain(domain: str) -> str:
    parsed = urlparse(domain)
    if parsed.scheme:
        return parsed.netloc.lower()
    return domain.lower().lstrip("www.")


def _should_visit(url: str, allowed_netlocs: set[str]) -> bool:
    parsed = urlparse(url)
    if not parsed.netloc:
        return True
    netloc = parsed.netloc.lower().lstrip("www.")
    return netloc in allowed_netlocs


def _parse_html(url: str, html: str) -> PageSnapshot:
    parser = _SimpleHTMLParser()
    parser.feed(html)
    return PageSnapshot(url=url, text=" ".join(parser.text_parts), links=parser.links)


def _extract_menu_links(links: Iterable[str]) -> List[str]:
    results: List[str] = []
    for link in links:
        if "menu" in link.lower() and link not in results:
            results.append(link)
    return results


def _extract_contact(text: str) -> dict:
    phones = set(re.findall(r"\\+?\d[\d\-\s]{7,}\d", text))
    emails = set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text))
    return {
        "phones": sorted(phones),
        "emails": sorted(emails),
    }


def build_profile(onboarding: models.Onboarding, allowed_domains: List[str]) -> dict:
    """Crawl a limited set of pages to build the web profile JSON."""

    if not allowed_domains:
        return {
            "primary_domain": "",
            "pages": [],
            "menu_links": [],
            "contact": {"phones": [], "emails": []},
            "about_snippet": "",
        }

    allowed_netlocs = { _normalize_domain(domain) for domain in allowed_domains if domain }
    if not allowed_netlocs:
        return {
            "primary_domain": "",
            "pages": [],
            "menu_links": [],
            "contact": {"phones": [], "emails": []},
            "about_snippet": "",
        }

    visited: set[str] = set()
    queue: deque[str] = deque()

    primary_domain = next(iter(allowed_netlocs))
    seed_url = allowed_domains[0]
    if not urlparse(seed_url).scheme:
        seed_url = f"https://{seed_url.strip('/') }"
    queue.append(seed_url)

    pages: List[PageSnapshot] = []

    while queue and len(pages) < _MAX_PAGES:
        current_url = queue.popleft()
        if current_url in visited:
            continue
        visited.add(current_url)

        try:
            started = time.monotonic()
            response = requests.get(current_url, timeout=_TIMEOUT_SECONDS)
            response.raise_for_status()
            html = response.text
            latency_ms = int((time.monotonic() - started) * 1000)
        except requests.RequestException as exc:
            logger.warning("Failed to fetch %s: %s", current_url, exc)
            continue

        snapshot = _parse_html(current_url, html)
        pages.append(snapshot)

        for link in snapshot.links:
            absolute = urljoin(current_url, link)
            if _should_visit(absolute, allowed_netlocs) and absolute not in visited:
                queue.append(absolute)

    combined_text = " ".join(snapshot.text for snapshot in pages)
    menu_links = []
    for snapshot in pages:
        for link in _extract_menu_links(snapshot.links):
            absolute = urljoin(snapshot.url, link)
            if absolute not in menu_links:
                menu_links.append(absolute)

    about_snippet = combined_text[:1000]
    contact = _extract_contact(combined_text)

    result = {
        "primary_domain": primary_domain,
        "pages": [snapshot.url for snapshot in pages],
        "menu_links": menu_links,
        "contact": contact,
        "about_snippet": about_snippet,
    }

    _log_profile_call({"primary_domain": primary_domain, "page_count": len(pages), "menu_links": len(menu_links)})
    return result
