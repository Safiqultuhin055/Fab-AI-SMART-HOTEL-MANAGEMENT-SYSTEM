from __future__ import annotations

from django.urls import path

from apps.housekeeping import views

app_name = "housekeeping"

urlpatterns = [
    path("", views.home, name="home"),
]
