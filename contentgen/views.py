"""Views for content generation blog."""
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.generic import DetailView, ListView
from celery.result import AsyncResult
from django.conf import settings

from .models import Article, ArticleRevision, Idea
from .pipeline import ContentPipeline
from .tasks import deep_research_task

class ArticleListView(ListView):
    """Public index of published articles."""

    queryset = Article.objects.filter(status=Article.STATUS_PUBLISHED).order_by("-published_at")
    paginate_by = 10
    template_name = "contentgen/article_list.html"

class ArticleDetailView(DetailView):
    """Display a single article."""

    model = Article
    slug_field = "slug"
    template_name = "contentgen/article_detail.html"

class TagFilteredView(ArticleListView):
    """Filter published articles by tag."""

    def get_queryset(self):
        tag = self.kwargs["tag"]
        base = super().get_queryset()
        ids = [a.id for a in base if tag in (a.idea.tags or [])]
        return base.filter(id__in=ids)


def pipeline_view(request):
    """Step through the content creation pipeline."""
    pipeline_state = request.session.get("pipeline", {})
    step = request.POST.get("step", "brainstorm")
    pipeline = ContentPipeline()

    if step == "brainstorm":
        ideas = pipeline.brainstorm_ideas()
        pipeline_state["ideas"] = ideas
        request.session["pipeline"] = pipeline_state
        context = {
            "step_title": "Brainstorm Ideas",
            "list_data": ideas,
            "next_step": "score",
        }
        return render(request, "contentgen/pipeline_step.html", context)

    if step == "score":
        ideas = pipeline_state.get("ideas", [])
        ranking = pipeline.score_and_pick(ideas)
        pipeline_state["ranking"] = ranking
        request.session["pipeline"] = pipeline_state
        context = {
            "step_title": "Ranked Ideas",
            "list_data": [f"{t} ({s:.2f})" for t, s in ranking],
            "next_step": "brief",
        }
        return render(request, "contentgen/pipeline_step.html", context)

    if step == "brief":
        ranking = pipeline_state.get("ranking", [])
        top = ranking[0][0] if ranking else "Untitled"
        brief = pipeline.make_brief(top)
        pipeline_state["brief"] = brief
        request.session["pipeline"] = pipeline_state
        context = {
            "step_title": "Brief",
            "text_data": brief,
            "next_step": "research",
        }
        return render(request, "contentgen/pipeline_step.html", context)

    if step == "research":
        task_id = pipeline_state.get("research_task_id")
        if not task_id:
            result = deep_research_task.delay(pipeline_state.get("brief", ""))
            if settings.CELERY_TASK_ALWAYS_EAGER:
                research = result.get()
                pipeline_state["research"] = research
                request.session["pipeline"] = pipeline_state
                context = {
                    "step_title": "Research",
                    "list_data": research.get("sources", []),
                    "next_step": "draft",
                }
                return render(request, "contentgen/pipeline_step.html", context)
            pipeline_state["research_task_id"] = result.id
            request.session["pipeline"] = pipeline_state
        result = AsyncResult(pipeline_state["research_task_id"])
        if result.ready():
            research = result.get()
            pipeline_state["research"] = research
            request.session["pipeline"] = pipeline_state
            context = {
                "step_title": "Research",
                "list_data": research.get("sources", []),
                "next_step": "draft",
            }
            return render(request, "contentgen/pipeline_step.html", context)
        return render(request, "contentgen/pipeline_wait.html", {"task_id": pipeline_state["research_task_id"]})

    if step == "draft":
        draft = pipeline.draft_article(pipeline_state.get("research", {}))
        pipeline_state["draft"] = draft
        request.session["pipeline"] = pipeline_state
        context = {
            "step_title": "Draft",
            "text_data": draft,
            "next_step": "edit",
        }
        return render(request, "contentgen/pipeline_step.html", context)

    if step == "edit":
        edited = pipeline.edit_article(pipeline_state.get("draft", ""))
        pipeline_state["edited"] = edited
        request.session["pipeline"] = pipeline_state
        context = {
            "step_title": "Edited Article",
            "text_data": edited,
            "next_step": "seo",
        }
        return render(request, "contentgen/pipeline_step.html", context)

    if step == "seo":
        seo = pipeline.make_seo(pipeline_state.get("edited", ""))
        pipeline_state["seo"] = seo
        request.session["pipeline"] = pipeline_state
        context = {
            "step_title": "SEO Metadata",
            "text_data": str(seo),
            "next_step": "format",
        }
        return render(request, "contentgen/pipeline_step.html", context)

    if step == "format":
        formatted = pipeline.format_article(pipeline_state.get("edited", ""))
        pipeline_state["formatted"] = formatted
        request.session["pipeline"] = pipeline_state
        context = {
            "step_title": "Formatted Article",
            "text_data": formatted,
            "next_step": "publish",
        }
        return render(request, "contentgen/pipeline_step.html", context)

    if step == "publish":
        ranking = pipeline_state.get("ranking", [])
        title = ranking[0][0] if ranking else "Untitled"
        idea = Idea.objects.create(title=title)
        article = Article.objects.create(title=title, slug=slugify(title), idea=idea)
        ArticleRevision.objects.create(
            article=article, step="formatted", content_md=pipeline_state.get("formatted", "")
        )
        pipeline_state["article_id"] = article.id
        request.session["pipeline"] = pipeline_state
        return render(request, "contentgen/pipeline_done.html", {"article": article})

    return redirect(reverse("contentgen:pipeline"))
