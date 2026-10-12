"""Django adapter.

Usage (``urls.py``)::

    from tekjuice_connector.adapters.django import create_view
    urlpatterns = [path("tekjuice/publish", create_view(receiver))]

The view is CSRF-exempt: the Data Engine authenticates with a bearer
credential, not a browser session.
"""

from __future__ import annotations

from tekjuice_connector.publication.receiver import PublicationReceiver


def create_view(receiver: PublicationReceiver):
    """Return a CSRF-exempt, POST-only Django view serving the receiver."""
    try:
        from django.http import HttpResponse, HttpResponseNotAllowed
        from django.views.decorators.csrf import csrf_exempt
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "tekjuice_connector.adapters.django requires Django. Install it, "
            "or use tekjuice_connector.adapters.wsgi.make_wsgi_app instead."
        ) from exc

    @csrf_exempt
    def receive_publication(request):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        limit = receiver.max_body_bytes + 1
        body = request.read(limit)
        result = receiver.handle(dict(request.headers), body)
        return HttpResponse(
            result.body_bytes(),
            status=result.status_code,
            content_type="application/json",
        )

    return receive_publication
