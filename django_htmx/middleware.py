"""Simplified HTMX middleware placeholder."""

from __future__ import annotations

from typing import Callable

from django.http import HttpRequest, HttpResponse


class HtmxMiddleware:
    """No-op middleware used to satisfy Django's configuration during tests."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)
