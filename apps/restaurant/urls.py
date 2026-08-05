from __future__ import annotations

from django.urls import path

from apps.restaurant import views

app_name = "restaurant"

urlpatterns = [
    path("", views.home, name="home"),
]
