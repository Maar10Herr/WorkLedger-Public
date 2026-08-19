from django.urls import path

from . import views

app_name = "travel"
urlpatterns = [
    path("enter/journey/", views.journey_entry, name="journey_entry"),
    path("enter/external-activity/", views.external_activity_entry, name="external_activity_entry"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/locations/", views.settings_locations, name="settings_locations"),
    path("settings/employer/", views.settings_employer, name="settings_employer"),
    path("settings/rail-passes/", views.settings_rail_passes, name="settings_rail_passes"),
    path("settings/routes/", views.settings_routes, name="settings_routes"),
    path("settings/security/", views.settings_security, name="settings_security"),
    path("settings/defaults/", views.settings_defaults, name="settings_defaults"),
    path("lookups/train/", views.train_lookup, name="train_lookup"),
    path(
        "journeys/recent/<str:action>/",
        views.recent_journey_action,
        name="recent_journey_action",
    ),
]
