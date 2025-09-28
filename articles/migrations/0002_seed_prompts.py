from django.db import migrations


def seed_prompts(apps, schema_editor):
    PromptTemplate = apps.get_model("articles", "PromptTemplate")
    defaults = {
        "ideas": """Generate 10 contrarian, owner-level article ideas about running an independent restaurant in the U.S.\nOutput JSON: { ideas: [{title, one_line, angle}], notes }""",
        "scoring": """Given these ideas and the rubric (business impact, originality, pain-point fit, evergreen potential),\nselect one winner. Output JSON: { winner:{title,summary,rationale}, ranked:[] }""",
        "outline": """Draft a detailed outline (H2/H3). Use web_search to find recent credible sources.\nReturn JSON: { outline: [...], sources: [{url,title,reason}] }""",
        "draft": """Write a draft article in markdown for independent restaurant owners.\nOutput JSON: { sections: [{h2, paragraphs[]}] }""",
        "polish": """Rewrite the draft to be professional, empathetic, and practical.\nKeep structure and citations intact.""",
        "seo": """Generate JSON: { slug, seo_title, seo_description, alt_text[], internal_links[] }.\nSlug must be lowercase kebab-case, ≤60 chars title, ≤160 chars description.""",
    }
    for name, text in defaults.items():
        PromptTemplate.objects.update_or_create(name=name, defaults={"prompt_text": text})


def unseed_prompts(apps, schema_editor):
    PromptTemplate = apps.get_model("articles", "PromptTemplate")
    PromptTemplate.objects.filter(name__in=["ideas", "scoring", "outline", "draft", "polish", "seo"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("articles", "0001_initial"),
    ]

    operations = [migrations.RunPython(seed_prompts, reverse_code=unseed_prompts)]
