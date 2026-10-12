# Receiving publications

Since 0.2.0 the connector can receive content published by the Tek Juice
Data Engine. The wire contract is in
[publication-contract.md](publication-contract.md).

## 1. Implement a ContentStore over your content model

The receiver never assumes a framework, ORM, table or URL scheme. You map
four required methods (and optionally `update` and `validate`) onto whatever
already serves your pages:

```python
from tekjuice_connector.publication import (
    ContentStoreError, DuplicateSlugError, PublicationContent, StoredContent,
)

class ArticleStore:
    def public_url(self, slug: str) -> str:
        return f"https://www.example.com/articles/{slug}"

    def find_by_slug(self, slug: str) -> StoredContent | None:
        article = Article.objects.filter(slug=slug).first()   # your ORM
        return _stored(article) if article else None

    def find_by_idempotency_key(self, key: str) -> StoredContent | None:
        article = Article.objects.filter(tekjuice_idempotency_key=key).first()
        return _stored(article) if article else None

    def create(self, content: PublicationContent, *, execution_id, idempotency_key):
        try:
            article = Article.objects.create(
                slug=content.slug, title=content.title, html=content.body,
                summary=content.excerpt, seo_description=content.meta_description,
                tekjuice_execution_id=execution_id,
                tekjuice_idempotency_key=idempotency_key,
                tekjuice_content_hash=content.content_hash(),
            )
        except IntegrityError as exc:        # unique slug lost a race
            raise DuplicateSlugError(f"Content slug already exists: {content.slug}") from exc
        except OperationalError as exc:      # database temporarily unavailable
            raise ContentStoreError(str(exc)) from exc
        return _stored(article)

    # Optional: omit to advertise create-only support.
    def update(self, existing: StoredContent, content, *, execution_id, idempotency_key):
        ...

    # Optional: extra checks run during verification dry runs.
    def validate(self, content: PublicationContent) -> None:
        ...
```

Rules: persist `execution_id` and `idempotency_key` atomically with the
content, keep slugs unique, raise `DuplicateSlugError` on a slug race and
`ContentStoreError` for transient failures. `find_by_slug` should return only
content you are willing to let Tek Juice manage.

Reference stores: `InMemoryContentStore` (tests) and `SQLiteContentStore`
(stdlib `sqlite3`, persistent).

## 2. Create the receiver and mount it

```python
import os
from tekjuice_connector.publication import PublicationReceiver

receiver = PublicationReceiver(
    store=ArticleStore(),
    website_id=os.environ["TEKJUICE_WEBSITE_ID"],
    receiver_secret=os.environ["TEKJUICE_RECEIVER_SECRET"],  # >= 32 chars
)
```

| Framework | Mount |
|---|---|
| FastAPI / Starlette | `app.include_router(tekjuice_connector.adapters.fastapi.create_router(receiver, path="/tekjuice/publish"))` |
| Flask | `app.register_blueprint(tekjuice_connector.adapters.flask.create_blueprint(receiver))` |
| Django | `path("tekjuice/publish", tekjuice_connector.adapters.django.create_view(receiver))` |
| Any ASGI server/framework | `tekjuice_connector.adapters.asgi.make_asgi_app(receiver)` |
| Any WSGI server/framework | `tekjuice_connector.adapters.wsgi.make_wsgi_app(receiver)` |
| Anything else | call `receiver.handle(headers, body_bytes)` and return its status and `body_bytes()` |

Frameworks are imported only by their adapter; none is a dependency.

## 3. Register the endpoint through the handshake

```python
from tekjuice_connector import TekJuiceConnector, PublicationRegistration

connector = TekJuiceConnector(
    publication=PublicationRegistration.from_receiver(
        receiver,
        endpoint_url="https://api.example.com/tekjuice/publish",
        # or set TEKJUICE_PUBLICATION_ENDPOINT_URL
    ),
)
result = connector.connect()
print(result.publication_status, result.next_action)
```

The Data Engine verifies the endpoint asynchronously with a dry run. Call
`connect()` again (for example on the next startup) to see
`publication_ready=True` and `ready=True`. If verification fails,
`publication_status` is `failed`; fix the cause and call `connect()` again.
