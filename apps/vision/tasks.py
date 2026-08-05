"""Vision background tasks.

Only the retention purge is implemented in P0 — it is a legal obligation
(goal.txt D10 #5), not a feature, so it ships before the feature that creates
the data it deletes.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("ashos.vision")


@shared_task(name="apps.vision.tasks.purge_expired_biometrics")
def purge_expired_biometrics() -> dict[str, int]:
    """Hard-delete face embeddings past ``expires_at``.

    Hard, not soft: a soft-deleted biometric row is still stored biometric data
    and would keep the hotel in breach of its own retention promise.
    """
    from django.apps import apps as django_apps
    from django.utils import timezone

    try:
        GuestFace = django_apps.get_model("vision", "GuestFace")
    except LookupError:
        # Model arrives in P3; the schedule exists earlier so the guarantee is
        # never accidentally shipped later than the data.
        return {"deleted": 0, "status": "model_not_ready"}

    qs = GuestFace.all_objects.filter(expires_at__lte=timezone.now())
    count = qs.count()
    if count:
        qs.hard_delete()
    logger.info("purged expired biometric embeddings", extra={"count": count})
    return {"deleted": count}
