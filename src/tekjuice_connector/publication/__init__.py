"""S2S publication: receive content from the Tek Juice Data Engine.

Quick start (any framework)::

    from tekjuice_connector.publication import PublicationReceiver

    receiver = PublicationReceiver(
        store=MyContentStore(),            # your ContentStore implementation
        website_id=os.environ["TEKJUICE_WEBSITE_ID"],
        receiver_secret=os.environ["TEKJUICE_RECEIVER_SECRET"],
    )

Then mount it with the adapter for your framework, e.g.
``tekjuice_connector.adapters.fastapi.create_router(receiver)``, and register
the public endpoint URL through the connector handshake.
"""

from tekjuice_connector.publication.contract import (
    CAPABILITY_CREATE,
    CAPABILITY_SEO_PRERENDER,
    CAPABILITY_SEO_SITEMAP,
    CAPABILITY_UPDATE,
    CAPABILITY_VERIFY,
    CONTRACT_VERSION,
    PublicationContent,
    PublishRequest,
    VerifyRequest,
)
from tekjuice_connector.publication.errors import (
    ContentStoreError,
    ContractViolationError,
    DuplicateSlugError,
    PublicationError,
    TargetNotFoundError,
    UnsupportedOperationError,
)
from tekjuice_connector.publication.receiver import PublicationReceiver, ReceiverResponse
from tekjuice_connector.publication.registration import PublicationRegistration
from tekjuice_connector.publication.seo import (
    AI_CRAWLERS,
    PublishedPage,
    SeoPages,
    build_robots_txt,
    build_sitemap,
    markdown_to_html,
    render_markdown,
    render_page,
)
from tekjuice_connector.publication.store import (
    ContentStore,
    InMemoryContentStore,
    SQLiteContentStore,
    StoredContent,
)

__all__ = [
    "CAPABILITY_CREATE",
    "CAPABILITY_SEO_PRERENDER",
    "CAPABILITY_SEO_SITEMAP",
    "CAPABILITY_UPDATE",
    "CAPABILITY_VERIFY",
    "CONTRACT_VERSION",
    "ContentStore",
    "ContentStoreError",
    "ContractViolationError",
    "DuplicateSlugError",
    "InMemoryContentStore",
    "PublicationContent",
    "PublicationError",
    "PublicationReceiver",
    "PublicationRegistration",
    "PublishedPage",
    "PublishRequest",
    "ReceiverResponse",
    "SQLiteContentStore",
    "SeoPages",
    "StoredContent",
    "TargetNotFoundError",
    "UnsupportedOperationError",
    "VerifyRequest",
    "AI_CRAWLERS",
    "build_robots_txt",
    "build_sitemap",
    "markdown_to_html",
    "render_markdown",
    "render_page",
]
