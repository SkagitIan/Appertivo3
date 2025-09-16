from django.contrib import admin
from django.urls import path
from django.contrib.auth import views as auth_views

from app import views as app_views

urlpatterns = [
    path("", app_views.home_view, name="home"),
    path("signup/", app_views.signup_view, name="signup"),
    path("login/", app_views.login_view, name="login"),
    path("logout/", app_views.logout_view, name="logout"),
    path("dashboard/", app_views.dashboard_redirect, name="dashboard-redirect"),
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
    path("concepts/<uuid:concept_id>/favorite/", app_views.concept_favorite_view, name="concept-favorite"),
    path("dishes/<uuid:concept_id>/", app_views.dishes_view, name="dishes"),
    path("dishes/<uuid:concept_id>/generate/", app_views.dishes_generate_view, name="dishes-generate"),
    path("dishes/favorite/<uuid:dish_id>/", app_views.dish_favorite_view, name="dish-favorite"),
    path("dishes/variation/<uuid:dish_id>/", app_views.dish_variation_view, name="dish-variation"),
    path("favorites/", app_views.favorites_view, name="favorites"),
    path("menus/", app_views.menus_view, name="menus"),
    path("favorites/remove/<str:type>/<uuid:id>/", app_views.favorite_remove_view, name="favorite-remove"),
    path("menus/create/", app_views.menu_collection_create_view, name="menu-collection-create"),
    path("menus/add/<uuid:dish_id>/<uuid:collection_id>/", app_views.menu_item_add_view, name="menu-item-add"),
    ##settings page
    path("settings/", app_views.settings_view, name="settings"),
    path("settings/info/", app_views.update_restaurant_info, name="update_restaurant_info"),
    path("settings/<uuid:restaurant_id>/rescrape/", app_views.rescrape_restaurant, name="rescrape_restaurant"),
    path(
        "settings/<uuid:restaurant_id>/rescrape-menu/",
        app_views.rescrape_menu,
        name="settings-rescrape-menu",
    ),
    path("settings/<uuid:restaurant_id>/update-creativity/", app_views.update_creativity, name="update_creativity"),
    path("settings/notifications/", app_views.update_notifications, name="update_notifications"),

    path("billing/", app_views.billing_view, name="billing"),
    path("billing/upgrade/", app_views.billing_upgrade_view, name="billing-upgrade"),
    path("billing/cancel/", app_views.billing_cancel_view, name="billing-cancel"),
    path("jobs/<uuid:job_id>/", app_views.job_status_view, name="job-status"),
    path("notifications/", app_views.notification_list_view, name="notification-list"),
    # Existing API and sample views
    path("api/signup/", app_views.signup_view, name="api-signup"),
    path("concepts-old/", app_views.concept_grid, name="concept-grid"),
    path("concepts-old/<str:concept_name>/dishes/", app_views.dish_grid, name="dish-grid"),
    path("admin/", admin.site.urls),
]
