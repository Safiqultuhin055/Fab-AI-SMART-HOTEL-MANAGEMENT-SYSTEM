from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from apps.accounts.forms import ProfileForm, StaffLoginForm
from apps.tenants.middleware import SESSION_KEY
from apps.tenants.models import HotelMembership


class StaffLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = StaffLoginForm
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs) | {"page_title": _("Sign in")}


class StaffLogoutView(LogoutView):
    next_page = reverse_lazy("accounts:login")


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, request.FILES or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Profile updated."))
        return redirect("accounts:profile")

    memberships = (
        HotelMembership.objects.filter(user=request.user)
        .select_related("hotel", "role")
        .order_by("-is_default")
    )
    return render(
        request,
        "accounts/profile.html",
        {"form": form, "memberships": memberships, "page_title": _("My profile")},
    )


@login_required
def switch_hotel(request, hotel_id):
    """Change the active property for this session.

    Membership is re-checked here rather than trusting the posted id — the URL
    is guessable and switching tenant is exactly the boundary an attacker would
    probe.
    """
    membership = HotelMembership.objects.filter(
        user=request.user, hotel_id=hotel_id, hotel__is_active=True
    ).first()
    if not membership:
        messages.error(request, _("You do not have access to that hotel."))
        return redirect("dashboard:home")

    request.session[SESSION_KEY] = str(hotel_id)
    messages.success(request, _("Switched to %(hotel)s.") % {"hotel": membership.hotel.name})
    return redirect("dashboard:home")
