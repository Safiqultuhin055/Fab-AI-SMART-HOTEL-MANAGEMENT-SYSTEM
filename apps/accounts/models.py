"""Users, roles and the audit trail.

Design notes
------------
*Email is the username.* Hotel staff turn over constantly and nobody remembers
an invented handle; email is already unique and already how you reset a login.

*Roles are data, not code.* ``Role`` rows own Django permissions, and a user
gets a role **per hotel** through ``tenants.HotelMembership``. A night manager
at one property can be a receptionist at another without a second account.

*The audit log is append-only.* It records who changed money, permissions or
biometric data, from which IP, under which request id (goal.txt §7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    Permission,
    PermissionsMixin,
)
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import ActiveModel, BaseModel
from apps.core.utils import uuid7

if TYPE_CHECKING:  # pragma: no cover
    pass


class RoleCode(models.TextChoices):
    """The role vocabulary. Five roles, deliberately.

    An earlier draft had one role per department (housekeeping, restaurant,
    accountant, auditor). In a 40-to-150-room property the same person covers
    several of those on a given shift, so the finer split produced accounts
    nobody used and permissions nobody could explain. Departmental separation,
    when a hotel actually needs it, is a custom ``Role`` row — the model already
    supports that.
    """

    SUPERADMIN = "superadmin", _("Super Admin")
    ADMIN = "admin", _("Admin")
    MANAGER = "manager", _("Manager")
    STAFF = "staff", _("Staff")
    AI_RECEPTION = "ai_reception", _("AI Reception")


class Role(BaseModel, ActiveModel):
    code = models.SlugField(_("code"), max_length=40, unique=True)
    name = models.CharField(_("name"), max_length=80)
    description = models.TextField(_("description"), blank=True)
    permissions = models.ManyToManyField(
        Permission, blank=True, related_name="ashos_roles", verbose_name=_("permissions")
    )
    is_system = models.BooleanField(
        _("system role"),
        default=False,
        help_text=_("Seeded by ASHOS. Cannot be deleted; permissions may be tuned."),
    )

    class Meta:
        verbose_name = _("role")
        verbose_name_plural = _("roles")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra: Any):
        if not email:
            raise ValueError("Users must have an email address.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra: Any):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra: Any):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra.get("is_staff") is not True or extra.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_staff=True and is_superuser=True.")
        return self._create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)

    email = models.EmailField(_("email"), unique=True, db_index=True)
    full_name = models.CharField(_("full name"), max_length=150)
    phone = models.CharField(_("phone"), max_length=32, blank=True)
    avatar = models.ImageField(upload_to="users/avatar/", blank=True, null=True)

    is_active = models.BooleanField(_("active"), default=True)
    is_staff = models.BooleanField(
        _("django admin access"),
        default=False,
        help_text=_("Access to /admin/. Operational roles do not need this."),
    )

    # --- Operational attributes ----------------------------------------------
    employee_code = models.CharField(_("employee code"), max_length=30, blank=True)
    preferred_language = models.CharField(_("language"), max_length=5, default="en")
    must_change_password = models.BooleanField(default=False)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ("full_name",)
        indexes = [models.Index(fields=["is_active", "email"])]

    def __str__(self) -> str:
        return f"{self.full_name} <{self.email}>"

    def get_full_name(self) -> str:
        return self.full_name

    def get_short_name(self) -> str:
        return self.full_name.split(" ")[0] if self.full_name else self.email

    @property
    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > timezone.now())

    def role_for(self, hotel_id) -> Role | None:
        membership = self.hotel_memberships.filter(hotel_id=hotel_id).select_related("role").first()
        return membership.role if membership else None


class AuditAction(models.TextChoices):
    CREATE = "create", _("Create")
    UPDATE = "update", _("Update")
    DELETE = "delete", _("Delete")
    LOGIN = "login", _("Login")
    LOGIN_FAILED = "login_failed", _("Failed login")
    LOGOUT = "logout", _("Logout")
    PERMISSION = "permission", _("Permission change")
    PAYMENT = "payment", _("Payment")
    REFUND = "refund", _("Refund")
    BIOMETRIC = "biometric", _("Biometric operation")
    AI_OVERRIDE = "ai_override", _("AI override / kill switch")
    EXPORT = "export", _("Data export")


class AuditLog(models.Model):
    """Append-only. No update path, no soft delete, no admin edit form."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    actor = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_entries"
    )
    actor_label = models.CharField(
        max_length=150,
        blank=True,
        help_text=_("Snapshot of the actor at the time; survives user deletion."),
    )
    hotel = models.ForeignKey(
        "tenants.Hotel", null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs"
    )

    action = models.CharField(max_length=20, choices=AuditAction.choices, db_index=True)
    object_type = models.CharField(max_length=100, blank=True, db_index=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    summary = models.CharField(max_length=255, blank=True)

    changes = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("{field: [old, new]}. Never contains secrets or raw biometrics."),
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    request_id = models.CharField(max_length=32, blank=True, db_index=True)

    class Meta:
        verbose_name = _("audit log entry")
        verbose_name_plural = _("audit log")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["hotel", "-created_at"]),
            models.Index(fields=["object_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.actor_label} {self.action} {self.summary}"
