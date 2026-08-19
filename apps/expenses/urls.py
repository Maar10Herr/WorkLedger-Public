from django.urls import path

from . import views

app_name = "expenses"
urlpatterns = [path("enter/expense/", views.expense_entry, name="expense_entry")]
