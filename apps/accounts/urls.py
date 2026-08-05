from __future__ import annotations

from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.StaffLoginView.as_view(), name="login"),
    path("logout/", views.StaffLogoutView.as_view(), name="logout"),
    path("profile/", views.profile, name="profile"),
    path("switch-hotel/<uuid:hotel_id>/", views.switch_hotel, name="switch_hotel"),
]
