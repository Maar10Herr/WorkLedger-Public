from django.urls import path

from . import views

app_name = "evidence"
urlpatterns = [
    path("enter/receipt/", views.receipt_inbox, name="receipt_inbox"),
    path(
        "receipts/<uuid:event_id>/reconcile/",
        views.reconcile_receipt_view,
        name="reconcile_receipt",
    ),
    path(
        "attachments/<uuid:attachment_id>/<str:variant>/",
        views.attachment_download,
        name="attachment_download",
    ),
]
