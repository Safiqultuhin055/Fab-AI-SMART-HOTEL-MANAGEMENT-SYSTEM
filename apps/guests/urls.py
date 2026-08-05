from __future__ import annotations

from django.urls import path

from apps.guests import views

app_name = "guests"

urlpatterns = [
    path("", views.home, name="home"),
]
