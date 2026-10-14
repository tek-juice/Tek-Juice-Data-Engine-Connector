"""Unit tests — publication contract parsing and receiver semantics."""

from __future__ import annotations

import pytest

from tekjuice_connector.publication import (
    InMemoryContentStore,
    PublicationReceiver,
    SQLiteContentStore,
)
from tekjuice_connector.publication.contract import parse_publish_request
from tekjuice_connector.publication.errors import (
    ContentStoreError,
    ContractViolationError,
)
from tests.fixtures.publication_fixtures import (
    AUTH_HEADERS,
    PUBLIC_BASE_URL,
    RECEIVER_SECRET,
    WEBSITE_ID,
    encode,
    publish_payload,
    verify_payload,
)


def _receiver(store=None, **kwargs):
    return PublicationReceiver(
        store=store or InMemoryContentStore(PUBLIC_BASE_URL),
        website_id=WEBSITE_ID,
        receiver_secret=RECEIVER_SECRET,
        **kwargs,
    )


def _send(receiver, payload, headers=AUTH_HEADERS):
    return receiver.handle(headers, encode(payload))


def _error_code(response):
    return response.body["error"]["code"]


# ── Construction ──────────────────────────────────────────────────────────────

def test_short_receiver_secret_is_rejected():
    with pytest.raises(ValueError, match="at least 32"):
        PublicationReceiver(
            store=InMemoryContentStore(PUBLIC_BASE_URL),
            website_id=WEBSITE_ID,
            receiver_secret="short",
        )


def test_store_missing_required_method_is_rejected():
    class Incomplete:
        def find_by_slug(self, slug):
            return None

    with pytest.raises(TypeError, match="find_by_idempotency_key"):
        _receiver(store=Incomplete())


def test_capabilities_reflect_store_support():
    assert _receiver().capabilities == {
        "publish.create", "publish.discovery", "publish.update", "publish.verify"
    }
    no_update = _receiver(store=InMemoryContentStore(PUBLIC_BASE_URL, supports_update=False))
    assert no_update.capabilities == {"publish.create", "publish.discovery", "publish.verify"}


def test_receiver_repr_never_contains_secret():
    receiver = _receiver()
    assert RECEIVER_SECRET not in repr(receiver)
    assert RECEIVER_SECRET not in str(receiver.receiver_secret)


# ── Authentication and request validation ────────────────────────────────────

@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer wrong-secret-" + "x" * 32},
        {"Authorization": "Bearer "},
    ],
)
def test_unauthenticated_requests_are_rejected(headers):
    store = InMemoryContentStore(PUBLIC_BASE_URL)
    response = _send(_receiver(store), publish_payload(), headers=headers)

    assert response.status_code == 401
    assert _error_code(response) == "unauthorized"
    assert store.find_by_slug("moving-cost-guide") is None


def test_header_names_are_case_insensitive():
    headers = {"authorization": f"Bearer {RECEIVER_SECRET}"}
    assert _send(_receiver(), publish_payload(), headers=headers).status_code == 200


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        (b"not json", 400, "malformed_request"),
        (b"[1, 2]", 400, "malformed_request"),
        (b"\xff\xfe", 400, "malformed_request"),
    ],
)
def test_malformed_bodies(body, status, code):
    response = _receiver().handle(AUTH_HEADERS, body)
    assert (response.status_code, _error_code(response)) == (status, code)


def test_oversized_body_is_rejected():
    response = _receiver(max_body_bytes=100).handle(AUTH_HEADERS, b"x" * 101)
    assert response.status_code == 413


def test_unsupported_contract_version_is_rejected():
    payload = publish_payload()
    payload["contract_version"] = "2.0"
    response = _send(_receiver(), payload)

    assert response.status_code == 422
    assert _error_code(response) == "unsupported_contract_version"


def test_request_for_other_website_is_rejected():
    response = _send(_receiver(), publish_payload(website_id="someone-else"))
    assert response.status_code == 403
    assert _error_code(response) == "website_mismatch"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(action="delete"),
        lambda p: p["content"].update(slug="ultimatemove.co.uk"),
        lambda p: p["content"].update(slug="/introduction"),
        lambda p: p["content"].update(slug="Intro"),
        lambda p: p["content"].pop("title"),
        lambda p: p["content"].update(body=""),
        lambda p: p["content"].update(meta_keywords="moving"),
        lambda p: p["content"].update(unexpected="x"),
        lambda p: p.update(unexpected="x"),
        lambda p: p.pop("idempotency_key"),
        lambda p: p["metadata"].update(content_hash="0" * 64),
        lambda p: p["metadata"].update(content_hash="not-a-hash"),
    ],
)
def test_contract_violations_are_permanent_422(mutate):
    store = InMemoryContentStore(PUBLIC_BASE_URL)
    payload = publish_payload()
    mutate(payload)

    response = _send(_receiver(store), payload)

    assert response.status_code == 422
    assert response.body["error"]["retryable"] is False
    assert store.find_by_slug("moving-cost-guide") is None


def test_content_hash_matches_data_engine_algorithm():
    payload = publish_payload()
    request = parse_publish_request(payload)
    assert request.content.content_hash() == payload["metadata"]["content_hash"]


def test_payload_without_content_hash_is_accepted():
    payload = publish_payload()
    payload["metadata"].pop("content_hash")
    assert parse_publish_request(payload).content.slug == "moving-cost-guide"


# ── create ───────────────────────────────────────────────────────────────────

def test_create_persists_and_returns_traceable_result():
    store = InMemoryContentStore(PUBLIC_BASE_URL)
    payload = publish_payload()

    response = _send(_receiver(store), payload)

    assert response.status_code == 200
    body = response.body
    assert body["status"] == "published"
    assert body["action_performed"] == "created"
    assert body["replayed"] is False
    assert body["persisted"] is True
    assert body["execution_id"] == payload["execution_id"]
    assert body["idempotency_key"] == payload["idempotency_key"]
    assert body["website_id"] == WEBSITE_ID
    assert body["content_hash"] == payload["metadata"]["content_hash"]
    assert body["published_url"] == f"{PUBLIC_BASE_URL}/moving-cost-guide"
    assert body["resource_id"]
    assert store.get_content("moving-cost-guide").title == "Moving Cost Guide"


def test_redelivered_create_is_replayed_not_duplicated():
    store = InMemoryContentStore(PUBLIC_BASE_URL)
    receiver = _receiver(store)
    payload = publish_payload()

    first = _send(receiver, payload)
    second = _send(receiver, payload)

    assert second.status_code == 200
    assert second.body["replayed"] is True
    assert second.body["action_performed"] == "none"
    assert second.body["resource_id"] == first.body["resource_id"]


def test_duplicate_slug_from_other_execution_is_409():
    store = InMemoryContentStore(PUBLIC_BASE_URL)
    receiver = _receiver(store)
    _send(receiver, publish_payload(slug="introduction", execution_id="execution-1"))

    response = _send(
        receiver,
        publish_payload(slug="introduction", execution_id="execution-2", title="Other"),
    )

    assert response.status_code == 409
    assert _error_code(response) == "duplicate_slug"
    assert response.body["error"]["retryable"] is False
    assert store.get_content("introduction").title == "Moving Cost Guide"


def test_regenerated_content_for_same_execution_updates_in_place():
    store = InMemoryContentStore(PUBLIC_BASE_URL)
    receiver = _receiver(store)
    first = _send(receiver, publish_payload())

    regenerated = publish_payload(body="<p>Regenerated body.</p>")
    response = _send(receiver, regenerated)

    assert response.status_code == 200
    assert response.body["action_performed"] == "updated"
    assert response.body["resource_id"] == first.body["resource_id"]
    assert store.get_content("moving-cost-guide").body == "<p>Regenerated body.</p>"


# ── update ───────────────────────────────────────────────────────────────────

def test_update_of_existing_content():
    store = InMemoryContentStore(PUBLIC_BASE_URL)
    receiver = _receiver(store)
    created = _send(receiver, publish_payload())

    response = _send(
        receiver,
        publish_payload(action="update", execution_id="execution-2", title="Updated"),
    )

    assert response.status_code == 200
    assert response.body["action_performed"] == "updated"
    assert response.body["resource_id"] == created.body["resource_id"]
    assert store.get_content("moving-cost-guide").title == "Updated"


def test_update_of_missing_target_is_404_and_creates_nothing():
    store = InMemoryContentStore(PUBLIC_BASE_URL)

    response = _send(_receiver(store), publish_payload(action="update", slug="resources"))

    assert response.status_code == 404
    assert _error_code(response) == "target_not_found"
    assert store.find_by_slug("resources") is None


def test_update_unsupported_by_store_is_clear_422():
    store = InMemoryContentStore(PUBLIC_BASE_URL, supports_update=False)
    response = _send(_receiver(store), publish_payload(action="update"))

    assert response.status_code == 422
    assert _error_code(response) == "unsupported_operation"


# ── verify ───────────────────────────────────────────────────────────────────

def test_verify_validates_without_persisting():
    store = InMemoryContentStore(PUBLIC_BASE_URL)

    response = _send(_receiver(store), verify_payload(challenge="one-time"))

    assert response.status_code == 200
    assert response.body == {
        "status": "verified",
        "contract_version": "1.0",
        "challenge": "one-time",
        "website_id": WEBSITE_ID,
        "persisted": False,
        "capabilities": ["publish.create", "publish.discovery", "publish.update", "publish.verify"],
        "contract_versions": ["1.0"],
        "would_publish_url": f"{PUBLIC_BASE_URL}/tekjuice-verification-0a1b2c",
    }
    assert store.find_by_slug("tekjuice-verification-0a1b2c") is None


def test_verify_requires_dry_run():
    payload = verify_payload()
    payload["dry_run"] = False
    assert _send(_receiver(), payload).status_code == 422


def test_verify_rejects_invalid_embedded_publication():
    payload = verify_payload()
    payload["publication"]["content"]["slug"] = "bad.slug"
    assert _send(_receiver(), payload).status_code == 422


def test_verify_with_wrong_secret_is_401():
    response = _send(
        _receiver(),
        verify_payload(),
        headers={"Authorization": "Bearer wrong-" + "x" * 40},
    )
    assert response.status_code == 401


def test_verify_runs_store_validation_hook():
    class StrictStore(InMemoryContentStore):
        def validate(self, content):
            if len(content.title) > 10:
                raise ContractViolationError("title exceeds the CMS limit of 10")

    response = _send(_receiver(StrictStore(PUBLIC_BASE_URL)), verify_payload())

    assert response.status_code == 422
    assert "CMS limit" in response.body["error"]["message"]


# ── Store failures ───────────────────────────────────────────────────────────

def test_transient_store_failure_is_retryable_503():
    class FlakyStore(InMemoryContentStore):
        def create(self, content, **kwargs):
            raise ContentStoreError("database is locked")

    response = _send(_receiver(FlakyStore(PUBLIC_BASE_URL)), publish_payload())

    assert response.status_code == 503
    assert response.body["error"]["retryable"] is True


def test_unexpected_store_exception_is_500_without_internals():
    class BrokenStore(InMemoryContentStore):
        def create(self, content, **kwargs):
            raise RuntimeError("password=hunter2 leaked in exception")

    response = _send(_receiver(BrokenStore(PUBLIC_BASE_URL)), publish_payload())

    assert response.status_code == 500
    assert "hunter2" not in str(response.body)


def test_store_returning_wrong_type_is_rejected():
    class LyingStore(InMemoryContentStore):
        def create(self, content, **kwargs):
            return {"published_url": "https://x"}

    response = _send(_receiver(LyingStore(PUBLIC_BASE_URL)), publish_payload())
    assert response.status_code == 500
    assert response.body["error"]["retryable"] is False


# ── SQLite reference store (real persistence) ────────────────────────────────

def test_sqlite_store_persists_and_survives_new_instance(tmp_path):
    path = str(tmp_path / "content.db")
    receiver = _receiver(SQLiteContentStore(path, PUBLIC_BASE_URL))

    created = _send(receiver, publish_payload())
    assert created.status_code == 200

    reopened = SQLiteContentStore(path, PUBLIC_BASE_URL)
    row = reopened.get_row("moving-cost-guide")
    assert row["title"] == "Moving Cost Guide"
    assert row["execution_id"] == "execution-1"
    assert row["resource_id"] == created.body["resource_id"]

    replay = _send(_receiver(reopened), publish_payload())
    assert replay.body["replayed"] is True

    duplicate = _send(
        _receiver(reopened), publish_payload(execution_id="execution-9")
    )
    assert duplicate.status_code == 409

    updated = _send(
        _receiver(reopened),
        publish_payload(action="update", execution_id="execution-9", title="New"),
    )
    assert updated.status_code == 200
    assert reopened.get_row("moving-cost-guide")["title"] == "New"


def test_sqlite_store_race_for_slug_resolves_to_conflict(tmp_path):
    """A concurrent writer wins the slug between lookup and insert."""
    path = str(tmp_path / "content.db")

    class RacingStore(SQLiteContentStore):
        def find_by_slug(self, slug):
            found = super().find_by_slug(slug)
            if found is None and not getattr(self, "_raced", False):
                self._raced = True
                SQLiteContentStore(path, PUBLIC_BASE_URL).create(
                    parse_publish_request(
                        publish_payload(execution_id="winner")
                    ).content,
                    execution_id="winner",
                    idempotency_key="winner-key",
                )
            return found

    response = _send(_receiver(RacingStore(path, PUBLIC_BASE_URL)), publish_payload())

    assert response.status_code == 409
    assert _error_code(response) == "duplicate_slug"
