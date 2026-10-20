# Content types: publishing to whatever your backend supports (0.10.0+)

Until 0.10 every publication was an article (`title`, `body`, `slug`, ...)
stored by your content store. A backend may support other content: products,
events, newsletter issues, job vacancies, or records only your application
knows. The connector now **discovers** the content types your backend
offers, keeps a **capability map** of them, **maps** each publication onto the
type's own schema, and publishes only where that is safe.

Nothing here replaces the existing setup: the handshake
(`POST /api/v1/connector/handshake`, `X-Tek-Juice-Connector-Key`), ownership
verification, the receiver secret, the S2S contract 1.0, idempotency and the
verify dry run work exactly as before. A publication that names no content
type goes to your content store as always.

## Where content types come from

| Source | How it is enabled | Typical backends |
|---|---|---|
| **Your content store** (`ContentStore`, or the sidecar content API's `/content` routes) | always; the default type, named `article` unless `TEKJUICE_DEFAULT_CONTENT_TYPE` says otherwise | any |
| **Content API manifest**: `GET {content API}/capabilities` returns `content_types` | each listed type is enabled (you listed it); `"enabled": false` lists it without enabling it | any language |
| **OpenAPI 3 document** on your backend (`TEKJUICE_OPENAPI_PATH`, e.g. `/openapi.json`) | an `x-tekjuice-content-type` extension on the create operation, or the allow-list | Laravel (Scribe, L5-Swagger), Django REST framework (drf-spectacular), FastAPI, NestJS, Express (swagger-jsdoc), Rails (rswag) |
| **WordPress REST** (`TEKJUICE_WORDPRESS_URL`, `_USER`, `_APP_PASSWORD`) | the allow-list | WordPress, plugins registering post types |

The allow-list `TEKJUICE_CONTENT_TYPES=product,event` enables discovered types
by name. A framework name is never treated as proof that a type exists or
can be published: only what your backend's own machine-readable description
says is used, and records are written only through the operations it declares.

## Status of a content type

| Status | Meaning |
|---|---|
| `discovered` | the backend lists it |
| `validated` | its schema was read and is usable, but it is **not enabled** for Tek Juice |
| `authorized` | enabled, but it has no create or update operation |
| `ready` | validated, enabled, and creatable (and/or updatable): Tek Juice may publish it |
| `unsupported` | not usable; `limitations` says why (no schema, a required read-only field, an endpoint on another host, a duplicate name, ...) |

A route existing is never enough for `ready`.

## The content API manifest

`GET {content API}/capabilities` may list content types besides your articles:

```json
{
  "update": true,
  "public_url_template": "https://www.example.com/blog/{slug}",
  "content_types": [
    {
      "name": "event",
      "label": "Events",
      "identity": "slug",
      "public_url_template": "https://www.example.com/events/{slug}",
      "schema": {
        "type": "object",
        "required": ["name", "slug", "starts_at"],
        "properties": {
          "name": {"type": "string", "maxLength": 200},
          "slug": {"type": "string"},
          "description": {"type": "string", "format": "html"},
          "starts_at": {"type": "string", "format": "date-time"},
          "capacity": {"type": "integer", "minimum": 1},
          "venue": {"type": "object", "required": ["city"],
                    "properties": {"city": {"type": "string"}}}
        }
      },
      "operations": {
        "create": {"method": "POST", "path": "/events"},
        "update": {"method": "PATCH", "path": "/events/{slug}"}
      },
      "field_map": {"description": "body"}
    },
    {
      "name": "newsletter_issue",
      "label": "Newsletter issues",
      "public": false,
      "schema": {"type": "object", "required": ["subject", "html", "slug"],
                 "properties": {"subject": {"type": "string"}, "html": {"type": "string", "format": "html"},
                                "slug": {"type": "string"}}},
      "operations": {
        "create": {"method": "POST", "path": "/issues"},
        "send": {"method": "POST", "path": "/issues/{slug}/send"}
      }
    }
  ]
}
```

- `schema` is a JSON Schema (`$ref` within the document and `allOf` are
  resolved). Types: string, `format: html` (rich text), `date`,
  `date-time`, `uri` (media when the name says image/photo/cover), `email`,
  integer, number, boolean, enum, object (nested), array; `readOnly`,
  `maxLength`, `minimum`/`maximum`, `default` and `nullable` are honoured.
  `x-tekjuice-type` (`rich_text`, `media`, `reference`, `slug`, `text`)
  overrides the guess.
- `operations` paths are relative to the content API and may use `{slug}`
  or `{id}`. They must be paths on the same host: absolute URLs, `//` and
  `..` are refused.
- `identity`: `slug` (records addressed by slug) or `id` (by the id your
  backend returns).
- `public: false`: records have no public page (a newsletter issue, an
  internal record): no public URL is reported or verified.
- `field_map`: explicit destination ← source mappings (see below).
- `workflow`: optional, informational (`{"states": [...], "default": "draft"}`).

Operations other than `create` and `update` (`send`, `publish`, `delete`,
`schedule`, ...) are recorded in the capability map but **never performed**
by Tek Juice: creating a newsletter issue never sends it to subscribers, and
creating a product never changes inventory.

## OpenAPI

A collection path with a JSON `POST` request body is a content type; the
item path (`/posts/{post}`) gives `update` (`PUT`/`PATCH`), `retrieve` and
`delete`; `POST /posts/{id}/publish`-style paths are recorded as extra
operations. The name comes from the last path segment (`/products` →
`product`) unless the extension names it:

```yaml
paths:
  /posts:
    post:
      x-tekjuice-content-type:
        name: post
        public_url_template: https://www.example.com/blog/{slug}
      requestBody:
        content:
          application/json:
            schema: {$ref: '#/components/schemas/PostInput'}
```

`x-tekjuice-content-type: true` enables a type with its derived name. The
document is read from your backend itself (`TEKJUICE_CONTENT_API_URL` +
`TEKJUICE_OPENAPI_PATH`) with the content API token.

## WordPress

`/wp-json/wp/v2/types?context=edit` lists the post types, and each type's
schema comes from `OPTIONS` on its collection. Authentication is a
WordPress **application password** (Users → Profile → Application
passwords) for a user who may edit those types. Internal types (media,
templates, navigation, global styles, fonts) are skipped. Records are
created as **drafts** unless the publication's fields set `status` (one of
the schema's values), so nothing goes live without your workflow's approval.

## How a publication is mapped

The S2S request may carry two new content fields (contract 1.0, only for
receivers advertising `publish.content_types`):

```json
"content": {
  "title": "Spring fair", "body": "<p>Join us.</p>", "slug": "spring-fair", "...": "...",
  "content_type": "event",
  "fields": {"starts_at": "2026-04-01T10:00:00+01:00", "venue": {"city": "Kampala"}}
}
```

The payload for the backend is built in this order:

1. **explicit `fields`**, by destination field name;
2. the type's **`field_map`** (`"description": "body"`, `"teaser": "fields.summary"`);
3. **semantic equivalents** for the standard values, only into type-compatible
   fields: title → `name`/`headline`/`subject`...; body → `content`/`html`/
   `body_html`...; excerpt → `summary`/`teaser`...; meta description →
   `seo_description`...; keywords → an array of strings `tags`/`keywords`.

Values are checked against the schema (type, enum, length, range, date-time
with a time zone, nested required fields). Rich text goes into rich-text
fields; a plain-text field receives the text without HTML. Media and
references are only taken from explicit values, never invented.

The publication is **refused, and nothing is written**, when a required field
has no value, a value does not fit, two destinations compete for one value
(set `field_map` or send the value explicitly), or the body would be lost.
The response carries the mapping report (`mapped`, `unmapped_sources`,
`missing_required`, `ambiguous`, `errors`). Values with no destination are
listed in `unmapped_sources`.

## Responses

A typed publication's response adds:

| Field | Meaning |
|---|---|
| `content_type` | the type the record was written to (the Data Engine checks it is the one it asked for) |
| `public` | whether records of this type have a public page |
| `state` | the backend's own state when it reports one (`draft`, `publish`, ...), else `published` (public) or `saved` (no public page) |

`published_url` is empty for types without a public page. The Data Engine
verifies the live page only for public records in a public state.

New error codes (HTTP 422, not retried):

| Code | Meaning | What to do |
|---|---|---|
| `content_type_unavailable` | unknown type, a type that is not ready, or an operation it does not offer; `reasons` says what is missing | enable the type (manifest, `x-tekjuice-content-type`, `TEKJUICE_CONTENT_TYPES`) or fix the limitation |
| `content_mapping_failed` | the content does not fit the schema; `mapping` is the report | send the missing fields, add `field_map`, or fix the schema |

## Idempotency, isolation and security

- Each typed record is kept in the receiver's ledger per **(content type,
  slug)** with its idempotency key: a retry replays the earlier result and
  creates nothing; the same slug in two types are two records. The backend
  also receives `Idempotency-Key`.
- One receiver serves one website: its capability map, ledger and
  credentials belong to that website only.
- Discovery and writes use your backend's base URL only; redirects are not
  followed; a public host needs https and a credential.
- Credentials travel only in headers. The capability map, diagnostics and
  errors never contain credentials or backend addresses.
- The capability map is cached for 15 minutes and re-read on the
  Data Engine's `capabilities` request; a type whose schema changed is
  validated again, and removed types disappear.

## Diagnostics

```
tekjuice content-types          # each type, its status, operations, required fields, what is missing
tekjuice content-types --json   # the capability map the Data Engine receives
```

The Data Engine shows the same map at
`GET /api/v1/websites/{id}/content-types` (`?refresh=true` for admins), with
where its articles go: your default type; or, when there is none, the one
ready editorial type. It never picks a type of another kind for an article,
and never guesses between several.

## Python backends

```python
from tekjuice_connector.publication import (
    ContentTypeRegistry, PublicationReceiver, native_source, openapi_source)

registry = ContentTypeRegistry(
    [native_source(store),
     openapi_source("http://127.0.0.1:8000/api", document_path="/openapi.json",
                    ledger_path="/var/lib/tekjuice/content-types.sqlite3", token=api_token)],
    allow=["product"])
receiver = PublicationReceiver(store=store, website_id=..., receiver_secret=..., content_types=registry)
```

A source is any callable returning `[(ContentTypeCapability, store)]`, so
other backends (a headless CMS SDK, a GraphQL client) can be added without
changing the connector.

## Limitations

- Tek Juice performs `create` and `update` only. Unpublishing, deleting,
  sending, scheduling and inventory changes are reported, never executed.
- GraphQL APIs are not discovered (no source yet). OpenAPI 2 (Swagger) is
  not read: convert it to OpenAPI 3.
- Media uploads (files) are not performed: media fields accept URLs given
  explicitly in `fields`.
- The Data Engine writes articles today; other content types are published
  when a publication names them (`content_type` and `fields` in the
  publication package). A generator for each kind of content is not part of
  this release.

## Validating against Laravel

`examples/laravel/` holds a real Laravel application's own files (an API
token middleware, `posts` and `products` migrations, Eloquent models,
controllers with `$request->validate()` rules, a JSON resource, `routes/api.php`
and an OpenAPI 3 document enabling only posts). `examples/laravel/run.sh`
creates a fresh Laravel app with Composer in Docker, adds those files and
serves it; `tests/integration/test_content_types_laravel.py` then checks,
against it:

- discovery: posts `ready` (identity `id`, Laravel's required fields), products
  `validated` but not enabled;
- a post created through `POST /api/posts`, updated through `PUT /api/posts/{id}`,
  a retry creating nothing, Laravel's default `status` kept;
- Laravel's own 422 messages (`The slug has already been taken.`) reported,
  and another post with that slug never overwritten;
- schema limits (`max:120`, the `status` enum) refused before Laravel is called;
- products refused until enabled with `TEKJUICE_CONTENT_TYPES`, then created
  with Laravel's rules (a missing `sku` refused first);
- a wrong token refused by Laravel and reported.

Other backends are checked by `tests/integration/test_content_types_real_backends.py`
(two Node.js backends with different conventions) and the unit tests.
