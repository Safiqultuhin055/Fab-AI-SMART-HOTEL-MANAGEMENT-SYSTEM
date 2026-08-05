from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm
from django.utils.translation import gettext_lazy as _

from apps.accounts.forms import StaffUserChangeForm, StaffUserCreationForm
from apps.accounts.models import AuditLog, Role, User


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_system", "is_active", "permission_count")
    list_filter = ("is_system", "is_active")
    search_fields = ("name", "code")
    filter_horizontal = ("permissions",)

    @admin.display(description="Permissions")
    def permission_count(self, obj: Role) -> int:
        return obj.permissions.count()

    def has_delete_permission(self, request, obj=None) -> bool:
        # System roles are referenced by memberships with PROTECT; deleting one
        # would either fail loudly or orphan access. Deactivate instead.
        if obj is not None and obj.is_system:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    add_form = StaffUserCreationForm
    form = StaffUserChangeForm
    change_password_form = AdminPasswordChangeForm
    model = User

    list_display = ("email", "full_name", "employee_code", "is_active", "is_staff", "last_login")
    list_filter = ("is_active", "is_staff", "is_superuser", "preferred_language")
    search_fields = ("email", "full_name", "employee_code", "phone")
    ordering = ("full_name",)
    readonly_fields = ("last_login", "last_login_ip", "failed_login_count", "created_at")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Identity"), {"fields": ("full_name", "phone", "avatar", "employee_code")}),
        (_("Preferences"), {"fields": ("preferred_language",)}),
        (
            _("Access"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "must_change_password",
                    "locked_until",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            _("Security trail"),
            {"fields": ("last_login", "last_login_ip", "failed_login_count", "created_at")},
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "full_name", "password1", "password2", "is_staff"),
            },
        ),
    )


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only by design — an editable audit trail is not an audit trail."""

    list_display = ("created_at", "actor_label", "hotel", "action", "object_type", "summary")
    list_filter = ("action", "hotel", "created_at")
    search_fields = ("actor_label", "summary", "object_id", "request_id")
    date_hierarchy = "created_at"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
