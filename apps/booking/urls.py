from __future__ import annotations

from django.urls import path

from apps.booking import views

app_name = "booking"

urlpatterns = [
    path("", views.home, name="home"),
    path("<str:code>/<str:verb>/", views.action, name="action"),
]
