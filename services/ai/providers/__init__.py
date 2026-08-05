"""Concrete AI backends.

Each module translates one vendor's wire format into ``services.ai.base``
types. Providers hold no business logic, no metering, no retry policy — the
gateway owns those so every backend gets them identically.
"""
