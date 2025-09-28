from django.urls import path

from . import views

app_name = "articles"

urlpatterns = [
    path("articles/", views.article_index, name="article_index"),
    path(
        "articles/<int:year>/<int:month>/<slug:slug>/",
        views.article_detail,
        name="article_detail",
    ),
]
