from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from app import outscraper, views as app_views
from articles import admin_views as articles_admin_views
from articles.sitemaps import ArticlesSitemap
from app.outscraper import outscraper_webhook
from app.admin_site import appertivo_admin_site

urlpatterns = [
    path("", app_views.home_view, name="home"),
    path("", include("appertivo.leads.urls")),
    path("", include("articles.urls")),
    path("signup/", app_views.signup_view, name="signup"),
    path("login/", app_views.login_view, name="login"),
    path("logout/", app_views.logout_view, name="logout"),
    path("privacy/", app_views.privacy_view, name="privacy"),
    path("terms/", app_views.terms_view, name="terms"),
    path("contact/", app_views.contact_view, name="contact"),
    path("dashboard/<uuid:restaurant_id>/", app_views.dashboard, name="dashboard"),

    ## onboarding
    path("onboarding/", app_views.onboarding_view, name="onboarding"),
    path("onboarding/status/", app_views.onboarding_status_view, name="onboarding-status"),
    path("onboarding/manual_menu/", app_views.manual_menu_view, name="manual-menu"),
    path("restaurants/<uuid:restaurant_id>/status/",app_views.restaurant_status,name="restaurant_status",),
    path("restaurants/<uuid:restaurant_id>/menu-modal/",app_views.show_menu_modal,name="show_menu_modal",),
    path("restaurants/<uuid:restaurant_id>/upload-menu/",app_views.upload_menu,name="upload_menu",),
    path("concepts/", app_views.concepts_view, name="concepts"),
    path("concepts/generate/", app_views.concepts_generate_view, name="concepts-generate"),
    path("concepts/favorites/", app_views.concepts_favorites_view, name="concepts-favorites"),
    path("concepts/<uuid:concept_id>/favorite/", app_views.concept_favorite_view, name="concept-favorite"),
    path("concepts/<uuid:concept_id>/background/",app_views.concept_background_view, name="concept-background",),
    path("search/", app_views.tag_search_view, name="tag-search"),
    path("dishes/<uuid:concept_id>/generate/", app_views.dishes_generate_view, name="dishes-generate"),
    path("dishes/<uuid:concept_id>/", app_views.dish_detail_view, name="dish_detail"),

    path("dishes/<uuid:dish_id>/favorite/", app_views.dish_favorite_view, name="dish_favorite"),
    path("dishes/variation/<uuid:dish_id>/", app_views.dish_variation_view, name="dish-variation"),
    path("dishes/<uuid:dish_id>/delete/", app_views.dish_delete_view, name="dish-delete"),
    path("favorites/", app_views.favorites_view, name="favorites"),
    path("menus/", app_views.menus_view, name="menus"),
    path("favorites/remove/<str:type>/<uuid:id>/", app_views.favorite_remove_view, name="favorite-remove"),
    path("menus/create/", app_views.menu_collection_create_view, name="menu-collection-create"),
    path("menus/add/<uuid:dish_id>/<uuid:collection_id>/", app_views.menu_item_add_view, name="menu-item-add"),
    path("menus/<uuid:collection_id>/rename/", app_views.menu_collection_update_view, name="menu-collection-rename"),
    path("menus/<uuid:collection_id>/delete/", app_views.menu_collection_delete_view, name="menu-collection-delete"),
    path("menus/<uuid:collection_id>/collaboration/", app_views.menu_collaboration_manage_view, name="menu-collaboration-manage"),
    path("menus/<uuid:collection_id>/feedback/", app_views.menu_feedback_review_view, name="menu-feedback-review"),
    path("menus/feedback/<uuid:feedback_id>/action/", app_views.menu_feedback_action_view, name="menu-feedback-action"),
    path("menus/item/move/", app_views.menu_item_move_view, name="menu-item-move"),
    path("collab/<uuid:link_id>/", app_views.collaboration_dashboard_view, name="collaboration-dashboard"),
    path("collab/<uuid:link_id>/feedback/", app_views.collaboration_feedback_submit_view, name="collaboration-feedback"),
    ##settings page
    path("settings/", app_views.settings_view, name="settings"),
    path("settings/info/", app_views.update_restaurant_info, name="update_restaurant_info"),
    path("settings/<uuid:restaurant_id>/rescrape/", app_views.rescrape_restaurant, name="rescrape_restaurant"),
    path("settings/<uuid:restaurant_id>/rescrape-menu/",app_views.rescrape_menu,name="settings-rescrape-menu",),
    path("settings/<uuid:restaurant_id>/update-creativity/", app_views.update_creativity, name="update_creativity"),
    path("settings/<uuid:restaurant_id>/refresh-reviews/", app_views.refresh_reviews, name="refresh_reviews"),
    path("settings/notifications/", app_views.update_notifications, name="update_notifications"),
    #outscraper webhook.
    path("outscraper-webhook/",outscraper_webhook, name="outscraper_webhook"),
    path("billing/", app_views.billing_view, name="billing"),
    path("billing/upgrade/", app_views.billing_upgrade_view, name="billing-upgrade"),
    path("billing/cancel/", app_views.billing_cancel_view, name="billing-cancel"),
    path("stripe/webhook/", app_views.stripe_webhook_view, name="stripe-webhook"),
    path("jobs/<uuid:job_id>/", app_views.job_status_view, name="job-status"),
    path("notifications/", app_views.notification_list_view, name="notification-list"),
    path("admin/articles/",articles_admin_views.dashboard_redirect,name="articles_admin_redirect",),
    path("sitemap.xml",sitemap,{"sitemaps": {"articles": ArticlesSitemap()}},name="sitemap",),
    # Existing API and sample views
    path("api/signup/", app_views.signup_view, name="api-signup"),
    path("admin/", appertivo_admin_site.urls),
]
