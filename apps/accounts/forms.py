from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserChangeForm, UserCreationForm
from django.utils.translation import gettext_lazy as _

User = get_user_model()


class StaffUserCreationForm(UserCreationForm):
    """Django's stock form is bound to ``auth.User``; rebind it to ours."""

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email", "full_name")


class StaffUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


class StaffLoginForm(AuthenticationForm):
    username = forms.EmailField(
        label=_("Email"),
        widget=forms.EmailInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "you@hotel.com",
                "autocomplete": "username",
                "autofocus": True,
            }
        ),
    )
    password = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "••••••••",
                "autocomplete": "current-password",
            }
        ),
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": _("Email or password is incorrect."),
        "inactive": _("This account is disabled. Contact your administrator."),
    }

    def confirm_login_allowed(self, user) -> None:
        super().confirm_login_allowed(user)
        if user.is_locked:
            raise forms.ValidationError(
                _("Account temporarily locked after repeated failed sign-ins. Try again shortly."),
                code="locked",
            )


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("full_name", "phone", "preferred_language", "avatar")
        widgets = {
            "full_name": forms.TextInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control"}),
            "preferred_language": forms.Select(
                choices=[("en", "English"), ("bn", "বাংলা")],
                attrs={"class": "form-select"},
            ),
            "avatar": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }
