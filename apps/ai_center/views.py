from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from apps.ai_center.models import ModelConfig, ModelKind, PromptTemplate, UsageLog
from apps.core.views import module_page
from services.ai import gateway, registry

#: The capabilities worth reporting on this page. ``face`` and ``ocr`` are here
#: even though their features are later phases — an operator setting a key up in
#: advance should be able to see that it resolved.
REPORTED_KINDS = ("llm", "stt", "tts", "embedding", "image_embedding", "face", "ocr")


def _resolution(tenant) -> list[dict]:
    """Where each capability's configuration is coming from, right now.

    Walks the same chain the gateway does — this hotel's row, then a
    platform-wide row, then the environment defaults — and says which link
    answered. "AI not configured" with seven rows on screen is unreadable
    without this.
    """
    tenant_id = str(tenant.pk) if tenant else None
    rows: list[dict] = []

    for kind in REPORTED_KINDS:
        model = registry.resolve(kind, tenant_id)
        source = "environment"
        if model.config_id:
            config = ModelConfig.all_objects.filter(pk=model.config_id).first()
            if config is not None:
                source = "this hotel" if config.tenant_id else "platform-wide"

        rows.append(
            {
                "kind": kind,
                "label": dict(ModelKind.choices).get(kind, kind),
                "provider": model.provider,
                "model": model.model_name,
                "source": source,
                "ready": gateway.capability_ready(kind, tenant_id),
            }
        )
    return rows


@login_required
@permission_required("core.access_ai_center", raise_exception=True)
def home(request):
    """AI Center control plane (SRS §6).

    This module has real data from Phase 0 — model configuration, versioned
    prompts and the usage log all exist — so it shows the real thing rather
    than a roadmap panel.
    """
    tenant = getattr(request, "tenant", None)
    since = timezone.now() - timedelta(days=7)

    usage = UsageLog.objects.all()
    if tenant:
        usage = usage.filter(tenant=tenant)
    recent = usage.filter(created_at__gte=since)

    totals = recent.aggregate(
        calls=Count("id"),
        cost=Sum("cost_usd"),
        latency=Avg("latency_ms"),
        failures=Count("id", filter=Q(success=False)),
        fallbacks=Count("id", filter=Q(fallback_used=True)),
        cached=Count("id", filter=Q(cache_hit=True)),
    )
    calls = totals["calls"] or 0

    by_module = list(
        recent.values("module")
        .annotate(calls=Count("id"), cost=Sum("cost_usd"), latency=Avg("latency_ms"))
        .order_by("-calls")[:8]
    )
    by_kind = list(
        recent.values("kind")
        .annotate(calls=Count("id"), latency=Avg("latency_ms"))
        .order_by("-calls")[:8]
    )

    # Every integration, not just the defaults — this page is the registry, and
    # an inactive or failing credential is exactly what an operator comes here
    # to find.
    #
    # Platform-wide rows are included even when a tenant is bound: they are what
    # this hotel actually runs on when it has no row of its own, and hiding them
    # is how "AI not configured" becomes unexplainable from this page.
    configs = ModelConfig.all_objects.filter(is_deleted=False)
    if tenant:
        configs = configs.filter(Q(tenant=tenant) | Q(tenant__isnull=True))

    templates = PromptTemplate.objects.prefetch_related("versions").order_by("key")

    return module_page(
        request,
        "ai_center",
        template="modules/ai_center.html",
        context={
            "ai_status": gateway.status(str(tenant.pk) if tenant else None),
            "configs": configs.order_by("kind", "provider", "name"),
            # Which row is actually serving this hotel, per capability, after the
            # own-row → platform → env chain has been walked. Without it an
            # operator is left comparing seven rows by eye to work out why the
            # kiosk is offline.
            "resolution": _resolution(tenant),
            # Read-only roles (front desk) see status and spend, not a nudge to
            # go and edit a credential they cannot edit.
            "can_configure": request.user.has_perm("ai_center.change_modelconfig"),
            "provider_summary": list(
                configs.values("provider")
                .annotate(total=Count("id"), live=Count("id", filter=Q(is_active=True)))
                .order_by("-total")
            ),
            "missing_keys": configs.filter(api_key="", is_active=True).count(),
            "prompts": templates,
            "stats": {
                "calls": calls,
                "cost": totals["cost"] or 0,
                "latency": int(totals["latency"] or 0),
                "failures": totals["failures"] or 0,
                "error_rate": (totals["failures"] or 0) / calls if calls else 0,
                "fallbacks": totals["fallbacks"] or 0,
                "cached": totals["cached"] or 0,
            },
            "by_module": by_module,
            "by_kind": by_kind,
            "recent_calls": recent.order_by("-created_at")[:15],
        },
    )
