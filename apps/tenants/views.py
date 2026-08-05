from __future__ import annotations

from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Count

from apps.accounts.models import Role
from apps.core.views import module_page
from apps.tenants.models import Hotel, HotelMembership


@login_required
@permission_required("core.access_settings", raise_exception=True)
def settings_home(request):
    """Hotel profile, staff and roles.

    Read-only for now: editing goes through the Django admin, which already has
    validation, audit and permission handling. Duplicating that into a custom
    form before Phase 1 would be work with no user benefit.
    """
    tenant = getattr(request, "tenant", None)

    memberships = (
        HotelMembership.all_objects.filter(hotel=tenant)
        .select_related("user", "role")
        .order_by("role__name", "user__full_name")
        if tenant
        else HotelMembership.all_objects.none()
    )

    roles = Role.objects.annotate(
        permission_count=Count("permissions", distinct=True),
        member_count=Count("memberships", distinct=True),
    ).order_by("name")

    return module_page(
        request,
        "settings",
        template="modules/settings.html",
        context={
            "memberships": memberships,
            "roles": roles,
            "hotels": Hotel.all_objects.order_by("code"),
        },
    )
