"""Business logic layer.

Every rule that a hotelier would recognise as a *policy* lives here — not in a
view, not in a serializer, not in a model method, not in a Celery task. Those
are all delivery mechanisms; this is the product.

Practical consequence: a service function takes plain arguments, returns plain
data or domain objects, raises ``apps.core.exceptions``, and never touches
``request``. That is what makes the same rule reusable from HTTP, WebSocket,
Celery, a management command and a test.
"""
