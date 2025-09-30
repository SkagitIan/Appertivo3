from django.urls import path

from . import views

app_name = "articles"

urlpatterns = [
    path("staff/articles/", views.staff_dashboard, name="staff_dashboard"),
    path("staff/articles/generate/", views.staff_generate_concepts, name="staff_generate_concepts"),
    path("staff/articles/concept/", views.staff_select_concept, name="staff_select_concept"),
    path("staff/articles/finalize/", views.staff_finalize_article, name="staff_finalize_article"),
    path("staff/articles/publish/", views.staff_publish_article, name="staff_publish_article"),
    path("staff/articles/runs/", views.staff_runs_fragment, name="staff_runs_fragment"),
    path("articles/", views.article_index, name="article_index"),
    path(
        "articles/<int:year>/<int:month>/<slug:slug>/",
        views.article_detail,
        name="article_detail",
    ),
]
