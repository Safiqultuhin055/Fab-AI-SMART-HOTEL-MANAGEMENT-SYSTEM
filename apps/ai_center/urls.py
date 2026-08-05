from __future__ import annotations

from django.urls import path

from apps.ai_center import views

app_name = "ai_center"

urlpatterns = [
    path("", views.home, name="home"),
]
