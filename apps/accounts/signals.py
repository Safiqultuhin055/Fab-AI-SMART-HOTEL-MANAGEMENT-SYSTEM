"""Authentication auditing and brute-force lockout.

Lockout policy: 5 consecutive failures locks the account for 15 minutes. Hotel
reception terminals sit in public lobbies, so an unlimited password-guess loop
against a known staff email is a real threat, not a theoretical one.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver
from django.utils import timezone

from apps.accounts.audit import record
from apps.accounts.models import AuditAction

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs) -> None:
    ip = request.META.get("REMOTE_ADDR") if request else None
    User = get_user_model()
    User.objects.filter(pk=user.pk).update(
        failed_login_count=0, locked_until=None, last_login_ip=ip
    )
    record(
        AuditAction.LOGIN,
        summary=f"{user.email} signed in",
        actor_id=str(user.pk),
        user_agent=request.META.get("HTTP_USER_AGENT", "") if request else "",
    )


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs) -> None:
    if user is None:
        return
    record(AuditAction.LOGOUT, summary=f"{user.email} signed out", actor_id=str(user.pk))


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs) -> None:
    # The key differs by caller: Django's login form sends ``username``, while
    # SimpleJWT sends the model's USERNAME_FIELD (``email``). Reading only one
    # of them silently disables the lockout on that path.
    creds = credentials or {}
    email = creds.get("username") or creds.get("email") or ""
    User = get_user_model()
    user = User.objects.filter(email__iexact=email).first()

    if user:
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED_ATTEMPTS:
            user.locked_until = timezone.now() + timedelta(minutes=LOCKOUT_MINUTES)
        user.save(update_fields=["failed_login_count", "locked_until", "updated_at"])

    record(
        AuditAction.LOGIN_FAILED,
        summary=f"failed sign-in for {email}",
        changes={"attempts": user.failed_login_count if user else 1},
    )
