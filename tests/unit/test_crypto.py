from __future__ import annotations

from apps.core.crypto import PREFIX, decrypt, encrypt


class TestFieldEncryption:
    def test_round_trip(self):
        assert decrypt(encrypt("sk-secret-value")) == "sk-secret-value"

    def test_ciphertext_is_not_plaintext(self):
        blob = encrypt("sk-secret-value")
        assert "sk-secret-value" not in blob
        assert blob.startswith(PREFIX)

    def test_double_encrypt_is_a_noop(self):
        """Re-saving a model must not wrap the value twice."""
        once = encrypt("value")
        assert encrypt(once) == once

    def test_empty_stays_empty(self):
        assert encrypt("") == ""
        assert decrypt("") == ""

    def test_legacy_plaintext_passes_through(self):
        assert decrypt("plain-value-written-before-encryption") == (
            "plain-value-written-before-encryption"
        )
