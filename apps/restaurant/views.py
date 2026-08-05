from __future__ import annotations

from django.contrib.auth.decorators import login_required, permission_required

from apps.core.views import module_page

MODULE_KEY = "restaurant"


@login_required
@permission_required("core.access_restaurant", raise_exception=True)
def home(request):
    """Module landing page.

    The permission check is here as well as on the sidebar: hiding a menu item
    is a UX affordance, not access control. Anyone can type the URL.
    """
    return module_page(request, MODULE_KEY)
