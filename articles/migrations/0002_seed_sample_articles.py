from django.db import migrations
from django.utils import timezone
from datetime import timedelta


def seed_sample_articles(apps, schema_editor):
    Article = apps.get_model("articles", "Article")
    if Article.objects.filter(status="published").exists():
        return

    now = timezone.now()
    samples = [
        {
            "title": "Decoding Weeknight Demand in 2025",
            "slug": "decoding-weeknight-demand-2025",
            "summary": "Operators are seeing lower Monday-Wednesday covers. Here’s how independent restaurants are filling those seats with smarter local offers.",
            "seo_description": "Learn how independents are balancing weeknight demand with targeted offers, localized social ads, and reservation pacing informed by Appertivo data.",
            "body_markdown": (
                "### What the data is telling us\n\n"
                "Operators across secondary markets report a 6-12% dip in early week covers. "
                "By pairing POS velocity with Appertivo demand projections, teams are staggering prix fixe specials, "
                "dynamic pricing, and private dining outreach to stabilize revenue.\n\n"
                "### Playbook\n\n"
                "- Package one dish that already wins weekend sentiment and adapt portioning for mid-week prep.\n"
                "- Promote via SMS two hours before the traditional lull; highlight scarcity, not discounts.\n"
                "- Collect feedback during service, tagging responses so the AI can spot repeatable signals.\n"
            ),
            "published_at": now - timedelta(days=14),
            "og_image_url": "https://placehold.co/1200x630?text=Weeknight+Demand",
        },
        {
            "title": "Four Ways to Train Staff on AI Tools Without Overwhelm",
            "slug": "train-staff-on-ai-tools",
            "summary": "Rollout anxiety is real. These four service-team rituals help you integrate AI workflows without slowing check times.",
            "seo_description": "Use Appertivo prompts, pre-shift rituals, and annotated menu tests to introduce AI to your restaurant team confidently.",
            "body_markdown": (
                "### Why it matters\n\n"
                "Guests notice when service teams are confident. AI platforms should support, not distract. "
                "Restaurants pairing Appertivo with short pre-shift reviews are seeing faster adoption and better guest anecdotes.\n\n"
                "### Try this\n\n"
                "1. Rotate a single prompt each week and let bartenders or servers test it before opening.\n"
                "2. Capture pain points in a shared doc; Appertivo can translate them into new training content automatically.\n"
                "3. Use the visual swipe deck during lineup so every teammate sees the same creative direction.\n"
                "4. Celebrate one AI win at the end of the night to make progress visible.\n"
            ),
            "published_at": now - timedelta(days=9),
            "og_image_url": "https://placehold.co/1200x630?text=Train+Staff+On+AI",
        },
        {
            "title": "Flavor Signals: Coastal Guests Are Craving Smoke",
            "slug": "flavor-signals-coastal-smoke",
            "summary": "Appertivo’s guest feedback model shows smoked elements outperforming citrus or chile notes in coastal cities this quarter.",
            "seo_description": "Smoked garnishes and charred aromatics are winning with coastal diners. See three deployable ideas with sourcing notes.",
            "body_markdown": (
                "### Signal\n\n"
                "Smoked elements are indexing +18% in positive sentiment across coastal markets. "
                "Guests cite 'surprising depth' and 'campfire nostalgia' across seafood, cocktails, and plant-forward dishes.\n\n"
                "### Deploy it\n\n"
                "- **Smoked mussel escabeche** layered with grilled fennel.\n"
                "- **Charred pineapple spritz** that leans savory with espellette salt.\n"
                "- **Roasted carrot steak** finished with smoked miso butter and seagreens.\n"
            ),
            "published_at": now - timedelta(days=4),
            "og_image_url": "https://placehold.co/1200x630?text=Flavor+Signals",
        },
    ]

    for payload in samples:
        Article.objects.create(
            title=payload["title"],
            slug=payload["slug"],
            summary=payload["summary"],
            seo_title=payload["title"],
            seo_description=payload["seo_description"],
            body_markdown=payload["body_markdown"],
            sources_json=[],
            status="published",
            published_at=payload["published_at"],
            og_image_url=payload["og_image_url"],
        )


def remove_sample_articles(apps, schema_editor):
    Article = apps.get_model("articles", "Article")
    slugs = [
        "decoding-weeknight-demand-2025",
        "train-staff-on-ai-tools",
        "flavor-signals-coastal-smoke",
    ]
    Article.objects.filter(slug__in=slugs).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("articles", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_sample_articles, remove_sample_articles),
    ]
