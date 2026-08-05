from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import redirect
from django.utils import timezone

from apps.billing.models import Folio, FolioStatus, Invoice, PaymentMethod
from apps.core.exceptions import ASHOSError
from apps.core.views import module_page
from services.analytics import kpi
from services.billing import folio as billing

MODULE_KEY = "billing"


@login_required
@permission_required("core.access_billing", raise_exception=True)
def home(request):
    hotel = getattr(request, "tenant", None)
    today = timezone.localdate()

    scope = request.GET.get("scope", "open")
    query = request.GET.get("q", "").strip()

    folios = Folio.all_objects.filter(tenant=hotel, is_deleted=False).select_related(
        "guest", "reservation"
    )
    if scope == "open":
        folios = folios.filter(status=FolioStatus.OPEN)
    elif scope == "outstanding":
        folios = folios.filter(balance__gt=0)
    elif scope == "settled":
        folios = folios.filter(status=FolioStatus.SETTLED)

    if query:
        folios = folios.filter(
            Q(number__icontains=query)
            | Q(reservation__code__icontains=query)
            | Q(guest__first_name__icontains=query)
            | Q(guest__last_name__icontains=query)
        )

    page = Paginator(folios.order_by("-opened_at"), 25).get_page(request.GET.get("page"))

    return module_page(
        request,
        MODULE_KEY,
        template="modules/billing.html",
        context={
            "page_obj": page,
            "scope": scope,
            "query": query,
            "revenue_today": kpi.revenue_today(hotel) if hotel else Decimal("0"),
            "payments_today": kpi.payments_today(hotel) if hotel else Decimal("0"),
            "outstanding": kpi.outstanding_balance(hotel) if hotel else Decimal("0"),
            "open_folios": Folio.all_objects.filter(
                tenant=hotel, is_deleted=False, status=FolioStatus.OPEN
            ).count(),
            "invoices": Invoice.all_objects.filter(tenant=hotel, is_deleted=False)
            .select_related("guest")
            .order_by("-issued_at")[:10],
            "invoiced_total": Invoice.all_objects.filter(
                tenant=hotel, is_deleted=False, issued_at__date=today
            ).aggregate(total=Sum("total"))["total"]
            or Decimal("0"),
            "methods": PaymentMethod.choices,
            "business_date": billing.business_date(hotel) if hotel else today,
        },
    )


@login_required
@permission_required("core.access_billing", raise_exception=True)
def take_payment(request, folio_id):
    """Record a payment against a folio.

    Cash and card only for now; the idempotency key stops a double-submitted
    form taking the money twice (goal.txt D16).
    """
    if request.method != "POST":
        return redirect("billing:home")

    hotel = getattr(request, "tenant", None)
    folio = Folio.all_objects.filter(pk=folio_id, tenant=hotel, is_deleted=False).first()
    if folio is None:
        messages.error(request, "Folio not found.")
        return redirect("billing:home")

    try:
        payment = billing.post_payment(
            folio,
            method=request.POST.get("method", PaymentMethod.CASH),
            amount=request.POST.get("amount") or folio.balance,
            reference=request.POST.get("reference", ""),
            idempotency_key=request.POST.get("idempotency_key", ""),
            user=request.user,
        )
        messages.success(
            request, f"{payment.get_method_display()} {payment.amount} recorded on {folio.number}."
        )
    except ASHOSError as exc:
        messages.error(request, exc.detail)

    return redirect(request.META.get("HTTP_REFERER") or "billing:home")


@login_required
@permission_required("core.access_billing", raise_exception=True)
def night_audit(request):
    if request.method != "POST":
        return redirect("billing:home")

    hotel = getattr(request, "tenant", None)
    try:
        result = billing.run_night_audit(hotel, user=request.user)
        messages.success(
            request,
            f"Night audit for {result['business_date']}: {result['rooms_charged']} room-night(s), "
            f"{result['revenue_posted']} posted. Business date is now "
            f"{result['next_business_date']}.",
        )
    except ASHOSError as exc:
        messages.error(request, exc.detail)

    return redirect("billing:home")
