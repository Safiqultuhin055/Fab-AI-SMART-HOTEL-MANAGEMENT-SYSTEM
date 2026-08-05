"""Custom model fields."""

from __future__ import annotations

from typing import Any

from django.db import models

from apps.core.crypto import decrypt, encrypt


class EncryptedTextField(models.TextField):
    """Transparently encrypted text.

    Stored ciphertext is prefixed (``enc:v1:``) so a future key/algorithm
    rotation can distinguish generations without a schema change.

    Trade-off, stated plainly: encrypted columns cannot be filtered, indexed or
    sorted server-side. Only use this for values that are read whole and never
    queried — API keys, secrets. Never for something you will search on.
    """

    def get_prep_value(self, value: Any) -> Any:
        if value is None:
            return value
        return encrypt(str(value))

    def from_db_value(self, value: Any, expression: Any, connection: Any) -> Any:  # noqa: ARG002
        if value is None:
            return value
        return decrypt(value)

    def to_python(self, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str) and value.startswith("enc:v1:"):
            return decrypt(value)
        return value
