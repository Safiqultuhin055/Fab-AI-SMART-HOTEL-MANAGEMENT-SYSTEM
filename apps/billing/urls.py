from __future__ import annotations

from django.urls import path

from apps.billing import views

app_name = "billing"

urlpatterns = [
    path("", views.home, name="home"),
    path("folio/<uuid:folio_id>/payment/", views.take_payment, name="take_payment"),
    path("night-audit/", views.night_audit, name="night_audit"),
]
