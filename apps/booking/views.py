from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect
from django.utils import timezone

from apps.booking.models import Reservation, ReservationStatus
from apps.core.exceptions import ASHOSError
from apps.core.views import module_page
from services.booking import availability
from services.booking import reservations as booking

MODULE_KEY = "reservations"


@login_required
@permission_required("core.access_reservations", raise_exception=True)
def home(request):
    hotel = getattr(request, "tenant", None)
    today = timezone.localdate()

    scope = request.GET.get("scope", "upcoming")
    query = request.GET.get("q", "").strip()

    bookings = (
        Reservation.all_objects.filter(tenant=hotel, is_deleted=False)
        .select_related("guest", "rate_plan")
        .prefetch_related("allocations__room")
    )

    scopes = {
        "arrivals": Q(
            check_in=today, status__in=[ReservationStatus.PENDING, ReservationStatus.CONFIRMED]
        ),
        "in_house": Q(status=ReservationStatus.CHECKED_IN),
        "departures": Q(check_out=today, status=ReservationStatus.CHECKED_IN),
        "upcoming": Q(check_in__gte=today)
        & ~Q(status__in=[ReservationStatus.CANCELLED, ReservationStatus.CHECKED_OUT]),
        "past": Q(status__in=[ReservationStatus.CHECKED_OUT, ReservationStatus.NO_SHOW]),
        "cancelled": Q(status=ReservationStatus.CANCELLED),
        "all": Q(),
    }
    bookings = bookings.filter(scopes.get(scope, Q()))

    if query:
        bookings = bookings.filter(
            Q(code__icontains=query)
            | Q(guest__first_name__icontains=query)
            | Q(guest__last_name__icontains=query)
            | Q(guest__phone__icontains=query)
        )

    order = "check_in" if scope in {"upcoming", "arrivals"} else "-check_in"
    page = Paginator(bookings.order_by(order, "-created_at"), 25).get_page(request.GET.get("page"))

    counts = {
        key: Reservation.all_objects.filter(tenant=hotel, is_deleted=False)
        .filter(condition)
        .count()
        for key, condition in scopes.items()
        if key != "all"
    }

    return module_page(
        request,
        MODULE_KEY,
        template="modules/reservations.html",
        context={
            "page_obj": page,
            "scope": scope,
            "query": query,
            "counts": counts,
            "today": today,
            "availability": (
                availability.by_type(hotel, today, today + timezone.timedelta(days=1))
                if hotel
                else []
            ),
        },
    )


@login_required
@permission_required("core.access_reservations", raise_exception=True)
def action(request, code: str, verb: str):
    """Check in, check out or cancel from the list.

    A thin wrapper: the service does the work and owns the rules, this only
    turns a refusal into a message the receptionist can read.
    """
    if request.method != "POST":
        return redirect("booking:home")

    hotel = getattr(request, "tenant", None)
    try:
        reservation = booking.find(hotel, code)
        if verb == "check-in":
            booking.check_in(reservation, user=request.user)
            messages.success(request, f"{reservation.guest.full_name} checked in.")
        elif verb == "check-out":
            invoice = booking.check_out(reservation, user=request.user)
            messages.success(request, f"Checked out. Invoice {invoice.number} issued.")
        elif verb == "cancel":
            booking.cancel(reservation, reason="cancelled at the desk", user=request.user)
            messages.success(request, f"Booking {reservation.code} cancelled.")
        else:
            messages.error(request, "Unknown action.")
    except ASHOSError as exc:
        messages.error(request, exc.detail)

    return redirect(request.META.get("HTTP_REFERER") or "booking:home")
