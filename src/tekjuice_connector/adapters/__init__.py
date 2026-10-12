"""Framework adapters for :class:`~tekjuice_connector.publication.PublicationReceiver`.

Each adapter only translates its framework's request/response objects; the
receiver holds every contract rule, so all frameworks behave identically.

- ``wsgi`` / ``asgi`` — dependency-free; mount under any WSGI or ASGI server
  or framework that can host a sub-application.
- ``fastapi``, ``flask``, ``django`` — thin native wrappers. The framework is
  imported only when the adapter is used and is never a package dependency.

A framework not listed here is supported through the WSGI/ASGI adapters, or
by calling ``receiver.handle(headers, body)`` directly from a route handler.
"""
