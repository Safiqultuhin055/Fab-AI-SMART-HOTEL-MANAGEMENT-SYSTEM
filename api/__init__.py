"""HTTP/JSON delivery layer.

Routers, serializers and viewsets only. Business rules live in ``services/``;
if a view is more than ~20 lines it is probably holding logic that belongs one
layer down.
"""
