# app/services/pipeline_runner.py
from .content_pipeline import (
    brainstorm_ideas, score_and_pick, make_brief,
    deep_research, draft_article, edit_article,
    make_seo, format_tailwind_html, CostMeter, MODEL
)
from .models import PipelineSession

PIPELINE_STEPS = [
    ("ideas", brainstorm_ideas),
    ("picked", score_and_pick),
    ("brief", make_brief),
    ("research", deep_research),
    ("draft", draft_article),
    ("edited", edit_article),
    ("seo", make_seo),
    ("html", format_tailwind_html),
]

def run_next_step(session: PipelineSession):
    """Run the next step for this pipeline session and save output."""
    cost = CostMeter(MODEL)

    step_order = [s[0] for s in PIPELINE_STEPS]
    try:
        idx = step_order.index(session.current_step)
    except ValueError:
        return session  # invalid step

    step_name, step_fn = PIPELINE_STEPS[idx]

    # prepare inputs based on step
    if step_name == "ideas":
        result = step_fn(session.topic_hint, cost)
    elif step_name == "picked":
        result = step_fn(session.ideas, cost)
    elif step_name == "brief":
        result = step_fn(session.picked, cost)
    elif step_name == "research":
        result = step_fn(session.topic_hint, cost)
    elif step_name == "draft":
        result = step_fn(session.brief, session.research, cost)
    elif step_name == "edited":
        result = step_fn(session.draft, cost)
    elif step_name == "seo":
        result = step_fn(session.edited, cost)
    elif step_name == "html":
        result = step_fn(session.edited)
    else:
        return session

    setattr(session, step_name, result)

    # advance to next step
    if idx + 1 < len(PIPELINE_STEPS):
        session.current_step = step_order[idx + 1]
    else:
        session.status = "completed"

    session.save()
    return session
