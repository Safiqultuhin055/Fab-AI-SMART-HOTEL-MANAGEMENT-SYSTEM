from __future__ import annotations

from django.urls import path

from apps.tenants import views

app_name = "tenants"

urlpatterns = [
    path("", views.settings_home, name="settings"),
]
