from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from apps.tenants.models import Hotel, HotelMembership

User = get_user_model()


class HotelSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Hotel
        fields = ("id", "code", "name", "city", "currency", "timezone", "accent_color")
        read_only_fields = fields


class MembershipSerializer(serializers.ModelSerializer):
    hotel = HotelSummarySerializer(read_only=True)
    role = serializers.CharField(source="role.code", read_only=True)
    role_name = serializers.CharField(source="role.name", read_only=True)

    class Meta:
        model = HotelMembership
        fields = ("hotel", "role", "role_name", "is_default")
        read_only_fields = fields


class MeSerializer(serializers.ModelSerializer):
    memberships = MembershipSerializer(source="hotel_memberships", many=True, read_only=True)
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "full_name",
            "phone",
            "avatar",
            "employee_code",
            "preferred_language",
            "is_superuser",
            "must_change_password",
            "memberships",
            "permissions",
        )
        read_only_fields = fields

    def get_permissions(self, obj) -> list[str]:
        """Permissions for the *current* hotel only.

        The client uses this to hide unusable controls. It is a UX affordance,
        never the enforcement point — the server re-checks on every call.
        """
        return sorted(obj.get_all_permissions())


class ASHOSTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Adds identity to the token payload and the login response.

    Saves the kiosk and PWA an extra round-trip on boot, and lets the staff UI
    render the right chrome before the first API call returns.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["email"] = user.email
        token["name"] = user.full_name
        membership = (
            HotelMembership.objects.filter(user=user, hotel__is_active=True)
            .select_related("hotel", "role")
            .order_by("-is_default")
            .first()
        )
        if membership:
            token["hotel"] = membership.hotel.code
            token["role"] = membership.role.code
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        data["user"] = MeSerializer(self.user, context=self.context).data
        return data


class AIHealthSerializer(serializers.Serializer):
    """Documents the AI health payload for OpenAPI; not used for input."""

    status = serializers.CharField()
    enabled = serializers.BooleanField()
    provider = serializers.CharField()
    model = serializers.CharField()
    configured = serializers.BooleanField()
    latency_ms = serializers.IntegerField(required=False)
    reply = serializers.CharField(required=False)
    cost_usd = serializers.CharField(required=False)
    detail = serializers.CharField(required=False)


class SystemHealthSerializer(serializers.Serializer):
    status = serializers.CharField()
    version = serializers.CharField()
    checks = serializers.DictField()
