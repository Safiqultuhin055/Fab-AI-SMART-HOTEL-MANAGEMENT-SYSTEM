from __future__ import annotations

from django.urls import path

from apps.rooms import views

app_name = "rooms"

urlpatterns = [
    path("", views.home, name="home"),
]
