from __future__ import annotations

from django.urls import path

from apps.reception import views

app_name = "reception"

urlpatterns = [
    path("", views.home, name="home"),
    path("kiosk/", views.kiosk, name="kiosk"),
]
