from django.urls import path

from . import views

app_name = "articles"

urlpatterns = [
    path("staff/articles/", views.staff_dashboard, name="staff_dashboard"),
    path("staff/articles/simple/", views.staff_simple_dashboard, name="staff_simple_dashboard"),
    path("staff/articles/simple/generate/", views.staff_simple_generate, name="staff_simple_generate"),
    path("staff/articles/ideas/", views.staff_idea_library, name="staff_idea_library"),
    path("staff/articles/ideas/<int:idea_id>/start/", views.staff_idea_start, name="staff_idea_start"),
    path("staff/articles/ideas/<int:idea_id>/archive/", views.staff_idea_archive, name="staff_idea_archive"),
    path("staff/articles/ideas/<int:idea_id>/delete/", views.staff_idea_delete, name="staff_idea_delete"),
    path("staff/articles/ideas/bulk/start/", views.staff_ideas_bulk_start, name="staff_ideas_bulk_start"),
    path("staff/articles/generate/", views.staff_generate_concepts, name="staff_generate_concepts"),
    path("staff/articles/concept/", views.staff_select_concept, name="staff_select_concept"),
    path("staff/articles/runs/<int:run_id>/status/", views.staff_draft_status, name="staff_draft_status"),
    path("staff/articles/runs/<int:run_id>/cancel/", views.staff_cancel_research, name="staff_cancel_research"),
    path("staff/articles/finalize/", views.staff_finalize_article, name="staff_finalize_article"),
    path("staff/articles/publish/", views.staff_publish_article, name="staff_publish_article"),
    path("staff/articles/runs/<int:run_id>/delete/", views.staff_delete_run, name="staff_delete_run"),
    path("staff/articles/runs/", views.staff_runs_fragment, name="staff_runs_fragment"),
    path("articles/", views.article_index, name="article_index"),
    path(
        "articles/<int:year>/<int:month>/<slug:slug>/",
        views.article_detail,
        name="article_detail",
    ),
]
