from django.urls import path

from . import views

app_name = "exports"
urlpatterns = [
    path("exports/", views.create_export, name="create_export"),
    path("exports/jobs/<uuid:job_id>/", views.export_job, name="export_job"),
    path("exports/<uuid:export_id>/download/", views.download_export, name="download_export"),
    path("employer-packages/", views.employer_packages, name="employer_packages"),
    path("employer-packages/<uuid:package_id>/", views.package_detail, name="package_detail"),
    path(
        "employer-packages/<uuid:package_id>/download/",
        views.download_package,
        name="download_package",
    ),
]
