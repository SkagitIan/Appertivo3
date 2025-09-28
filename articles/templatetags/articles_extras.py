from __future__ import annotations

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter(name="article_markdown")
def article_markdown(value: str) -> str:
    if not value:
        return ""

    blocks = []
    for raw_block in value.split("\n\n"):
        block = raw_block.strip()
        if not block:
            continue
        if block.startswith("### "):
            heading = escape(block[4:].strip())
            blocks.append(f"<h3 class='text-xl font-semibold text-gray-900 mt-8'>{heading}</h3>")
            continue
        if block.startswith("## "):
            heading = escape(block[3:].strip())
            blocks.append(f"<h2 class='text-2xl font-bold text-gray-900 mt-10'>{heading}</h2>")
            continue
        if block.startswith("- "):
            items = []
            for line in block.splitlines():
                if line.startswith("- "):
                    items.append(f"<li class='ml-4 list-disc'>{escape(line[2:].strip())}</li>")
            if items:
                blocks.append("<ul class='space-y-2 text-gray-700'>%s</ul>" % "".join(items))
                continue
        paragraph = " ".join(escape(line.strip()) for line in block.splitlines())
        blocks.append(f"<p class='text-gray-700 leading-relaxed mt-4'>{paragraph}</p>")

    return mark_safe("\n".join(blocks))
