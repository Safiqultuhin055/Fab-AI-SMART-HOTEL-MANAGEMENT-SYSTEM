"""The single AI gateway (goal.txt D07).

Nothing outside this package may import an AI SDK. Everything else calls::

    from services.ai import gateway

    result = gateway.chat(messages, module="reception")
    vector = gateway.embed("sea view room with balcony")

Why one door: model and vendor choices change every few months, and the cost of
that change must be one config row — not a grep across forty call sites. It is
also the only place where metering, kill-switch checks, budget caps, retries and
fallback can be enforced *for every call*, including the ones a future developer
adds without reading the guidelines.
"""

from services.ai import gateway
from services.ai.base import ChatMessage, ChatResult, EmbeddingResult, Role

__all__ = ["ChatMessage", "ChatResult", "EmbeddingResult", "Role", "gateway"]
