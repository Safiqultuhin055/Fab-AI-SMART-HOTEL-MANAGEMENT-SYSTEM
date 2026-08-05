from __future__ import annotations

from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Count, Q

from apps.core.views import module_page
from apps.guests.models import Guest, GuestTier

MODULE_KEY = "guests"


@login_required
@permission_required("core.access_guests", raise_exception=True)
def home(request):
    hotel = getattr(request, "tenant", None)
    query = request.GET.get("q", "").strip()
    tier = request.GET.get("tier", "")

    guests = (
        Guest.all_objects.filter(tenant=hotel, is_deleted=False)
        .annotate(booking_count=Count("reservations", filter=Q(reservations__is_deleted=False)))
        .order_by("-last_stay_at", "last_name")
    )

    if query:
        # Front desk searches by whatever the guest just said: a name, a phone
        # number, or the email on the booking confirmation.
        guests = guests.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
        )
    if tier:
        guests = guests.filter(tier=tier)

    page = Paginator(guests, 25).get_page(request.GET.get("page"))

    return module_page(
        request,
        MODULE_KEY,
        template="modules/guests.html",
        context={
            "page_obj": page,
            "query": query,
            "tier": tier,
            "tiers": GuestTier.choices,
            "total": guests.count(),
        },
    )
