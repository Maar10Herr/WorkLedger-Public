from django.urls import path

from . import views

app_name = "ledger"
urlpatterns = [
    path("enter/", views.enter, name="enter"),
    path("enter/work-from-home/", views.create_wfh, name="create_wfh"),
    path("events/<uuid:event_id>/undo/", views.undo_event, name="undo_event"),
    path("history/", views.history, name="history"),
    path("unresolved/", views.unresolved, name="unresolved"),
    path("status/", views.system_status, name="system_status"),
    path("history/<uuid:event_id>/", views.event_detail, name="event_detail"),
    path("history/<uuid:event_id>/correct/", views.correct_event, name="correct_event"),
]
