"""Flask adapter.

Usage::

    from tekjuice_connector.adapters.flask import create_blueprint
    app.register_blueprint(create_blueprint(receiver, path="/tekjuice/publish"))
"""

from __future__ import annotations

from tekjuice_connector.publication.receiver import PublicationReceiver


def create_blueprint(
    receiver: PublicationReceiver,
    *,
    path: str = "/tekjuice/publish",
    name: str = "tekjuice_publication",
):
    """Return a Flask ``Blueprint`` with a single POST route serving the receiver."""
    try:
        from flask import Blueprint, Response, request
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "tekjuice_connector.adapters.flask requires Flask. Install it, "
            "or use tekjuice_connector.adapters.wsgi.make_wsgi_app instead."
        ) from exc

    blueprint = Blueprint(name, __name__)

    def receive_publication():
        limit = receiver.max_body_bytes + 1
        body = request.stream.read(limit)
        result = receiver.handle(dict(request.headers), body)
        return Response(
            result.body_bytes(),
            status=result.status_code,
            mimetype="application/json",
        )

    blueprint.add_url_rule(
        path, "receive_publication", receive_publication, methods=["POST"]
    )
    return blueprint
