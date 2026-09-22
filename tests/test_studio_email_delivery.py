"""Tests for encrypted, durable Studio invitation email delivery."""

from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from svix.webhooks import Webhook

import tracebisect.studio.api as studio_api
import tracebisect.studio.email_delivery as email_delivery
from tracebisect.cli import main
from tracebisect.studio.access_keys import create_studio_api_key
from tracebisect.studio.auth import StudioAuthConfig
from tracebisect.studio.email_delivery import (
    StudioEmailBatchResult,
    StudioEmailDelivery,
    StudioEmailDeliveryConflict,
    StudioEmailMessage,
)
from tracebisect.studio.identity import create_studio_invitation, revoke_studio_invitation
from tracebisect.studio.storage import StudioConfigurationError, StudioStoreRegistry

IDENTITY_SECRET = "email-test-identity-secret-with-more-than-thirty-two-characters"
PEPPER = "email-test-api-key-pepper-with-more-than-thirty-two-characters"
WEBHOOK_SECRET = "whsec_" + base64.b64encode(b"tracebisect-test-webhook-signing-secret").decode()


def _env(database: Path) -> dict[str, str]:
    return {
        "TRACEBISECT_STUDIO_STORAGE": "sqlite",
        "TRACEBISECT_STUDIO_SQLITE_PATH": str(database),
        "TRACEBISECT_STUDIO_WORKSPACE_ID": "health-probe",
        "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
        "TRACEBISECT_STUDIO_API_KEY_PEPPER": PEPPER,
        "TRACEBISECT_STUDIO_IDENTITY_SECRET": IDENTITY_SECRET,
        "TRACEBISECT_STUDIO_EMAIL_PROVIDER": "resend",
        "TRACEBISECT_STUDIO_EMAIL_FROM": "TraceBisect <invites@example.com>",
        "TRACEBISECT_STUDIO_EMAIL_REPLY_TO": "support@example.com",
        "TRACEBISECT_STUDIO_PUBLIC_URL": "https://studio.example.com",
        "RESEND_API_KEY": "re_test_key_long_enough_for_validation",
    }


def _issued_invitation(database: Path):
    return create_studio_invitation(
        database,
        workspace_id="workspace-a",
        email="new.person@example.com",
        role="viewer",
        expires_in_days=7,
        identity_secret_value=IDENTITY_SECRET,
    )


def _webhook(
    *,
    event_id: str,
    event_type: str,
    provider_message_id: str,
    event_created_at: datetime,
    signed_at: datetime,
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {
            "type": event_type,
            "created_at": event_created_at.isoformat().replace("+00:00", "Z"),
            "data": {"email_id": provider_message_id},
        },
        separators=(",", ":"),
    )
    return body.encode(), {
        "svix-id": event_id,
        "svix-timestamp": str(int(signed_at.timestamp())),
        "svix-signature": Webhook(WEBHOOK_SECRET).sign(event_id, signed_at, body),
    }


class _SuccessfulTransport:
    def __init__(self) -> None:
        self.messages: list[StudioEmailMessage] = []
        self.idempotency_keys: list[str] = []

    def send(self, message: StudioEmailMessage, *, idempotency_key: str) -> str:
        self.messages.append(message)
        self.idempotency_keys.append(idempotency_key)
        return "provider-message-123"


class _RetryThenSuccessTransport(_SuccessfulTransport):
    def send(self, message: StudioEmailMessage, *, idempotency_key: str) -> str:
        if not self.messages:
            self.messages.append(message)
            self.idempotency_keys.append(idempotency_key)
            raise email_delivery._TransientProviderError("resend_http_503")
        return super().send(message, idempotency_key=idempotency_key)


class _PermanentFailureTransport:
    def send(self, message: StudioEmailMessage, *, idempotency_key: str) -> str:
        raise email_delivery._PermanentProviderError("resend_http_422")


class _OneCycleEvent:
    def __init__(self) -> None:
        self.stopped = False

    def is_set(self) -> bool:
        return self.stopped

    def set(self) -> None:
        self.stopped = True

    def wait(self, _seconds: int) -> bool:
        self.stopped = True
        return True


def test_email_configuration_is_explicit_and_https_safe(tmp_path: Path) -> None:
    assert StudioEmailDelivery.from_env({}).enabled is False
    invalid = _env(tmp_path / "studio.db")
    invalid["TRACEBISECT_STUDIO_PUBLIC_URL"] = "http://studio.example.com"
    with pytest.raises(StudioConfigurationError, match="must use HTTPS"):
        StudioEmailDelivery.from_env(invalid)
    postgres_without_store = _env(tmp_path / "unused.db")
    postgres_without_store["TRACEBISECT_STUDIO_STORAGE"] = "postgres"
    postgres_without_store.pop("TRACEBISECT_STUDIO_SQLITE_PATH")
    with pytest.raises(StudioConfigurationError, match="configured PostgreSQL store"):
        StudioEmailDelivery.from_env(postgres_without_store)


def test_invitation_payload_is_encrypted_and_sent_with_stable_idempotency(
    tmp_path: Path,
) -> None:
    database = tmp_path / "studio.db"
    issued = _issued_invitation(database)
    delivery = StudioEmailDelivery.from_env(_env(database))
    queued = delivery.queue_invitation(issued)

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT payload_ciphertext, status, attempt_count FROM studio_email_outbox"
        ).fetchone()
    assert row is not None
    assert str(row[0]).startswith("v1.")
    assert issued.invitation_token not in str(row[0])
    assert "studio.example.com" not in str(row[0])
    assert row[1:] == ("pending", 0)

    transport = _SuccessfulTransport()
    sent = delivery.deliver_message(queued.message_id, transport=transport)
    assert sent is not None
    assert sent.status == "sent"
    assert sent.attempt_count == 1
    assert transport.messages[0].to == "new.person@example.com"
    assert issued.invitation_token in transport.messages[0].text
    assert transport.idempotency_keys == [f"tracebisect-invite-{queued.message_id}"]


def test_transient_failure_retries_without_changing_idempotency_key(tmp_path: Path) -> None:
    database = tmp_path / "studio.db"
    delivery = StudioEmailDelivery.from_env(_env(database))
    started = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)
    queued = delivery.queue_invitation(_issued_invitation(database), now=started)
    transport = _RetryThenSuccessTransport()

    retry = delivery.deliver_message(queued.message_id, transport=transport, now=started)
    assert retry is not None
    assert retry.status == "retry"
    assert retry.last_error_code == "resend_http_503"
    assert delivery.deliver_due(transport=transport, now=started).examined == 0

    result = delivery.deliver_due(
        transport=transport,
        now=started + timedelta(seconds=31),
    )
    assert result.sent == 1
    assert transport.idempotency_keys[0] == transport.idempotency_keys[1]


def test_reclaimed_delivery_lease_rejects_the_stale_worker(tmp_path: Path) -> None:
    database = tmp_path / "studio.db"
    delivery = StudioEmailDelivery.from_env(_env(database))
    started = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)
    queued = delivery.queue_invitation(_issued_invitation(database), now=started)
    first = email_delivery._claim_message(database, message_id=queued.message_id, current=started)
    assert first is not None
    second = email_delivery._claim_message(
        database,
        message_id=queued.message_id,
        current=started + timedelta(seconds=email_delivery.EMAIL_DELIVERY_LEASE_SECONDS + 1),
    )
    assert second is not None
    assert second.lease_token != first.lease_token

    with pytest.raises(StudioEmailDeliveryConflict, match="lease is no longer active"):
        email_delivery._mark_sent(
            database,
            first.record,
            lease_token=first.lease_token,
            provider_message_id="stale-provider-message",
            current=started,
        )


def test_worker_never_sends_a_revoked_invitation(tmp_path: Path) -> None:
    database = tmp_path / "studio.db"
    delivery = StudioEmailDelivery.from_env(_env(database))
    issued = _issued_invitation(database)
    queued = delivery.queue_invitation(issued)
    revoke_studio_invitation(
        database,
        workspace_id="workspace-a",
        invitation_id=issued.record.invitation_id,
    )
    transport = _SuccessfulTransport()

    failed = delivery.deliver_message(queued.message_id, transport=transport)
    assert failed is not None
    assert failed.status == "failed"
    assert transport.messages == []
    assert delivery.latest_for_workspace("workspace-a")[issued.record.invitation_id].status == (
        "failed"
    )


def test_failed_email_can_be_requeued_without_exposing_the_invitation(tmp_path: Path) -> None:
    database = tmp_path / "studio.db"
    delivery = StudioEmailDelivery.from_env(_env(database))
    issued = _issued_invitation(database)
    first = delivery.queue_invitation(issued)
    failed = delivery.deliver_message(first.message_id, transport=_PermanentFailureTransport())
    assert failed is not None
    assert failed.status == "failed"

    second = delivery.requeue_invitation(
        workspace_id="workspace-a",
        invitation_id=issued.record.invitation_id,
    )
    assert second.message_id != first.message_id
    with sqlite3.connect(database) as connection:
        ciphertext = connection.execute(
            "SELECT payload_ciphertext FROM studio_email_outbox WHERE message_id = ?",
            (second.message_id,),
        ).fetchone()
    assert ciphertext is not None
    assert issued.invitation_token not in str(ciphertext[0])


def test_signed_webhooks_are_deduplicated_and_ordered_by_event_time(tmp_path: Path) -> None:
    database = tmp_path / "studio.db"
    env = _env(database)
    env["RESEND_WEBHOOK_SECRET"] = WEBHOOK_SECRET
    delivery = StudioEmailDelivery.from_env(env)
    queued = delivery.queue_invitation(_issued_invitation(database))
    sent = delivery.deliver_message(queued.message_id, transport=_SuccessfulTransport())
    assert sent is not None
    assert sent.provider_status == "accepted"
    signed_at = datetime.now(timezone.utc)
    delivered_body, delivered_headers = _webhook(
        event_id="msg_webhook_delivered_123",
        event_type="email.delivered",
        provider_message_id="provider-message-123",
        event_created_at=signed_at,
        signed_at=signed_at,
    )

    delivered = delivery.process_webhook(delivered_body, delivered_headers)
    duplicate = delivery.process_webhook(delivered_body, delivered_headers)
    delayed_body, delayed_headers = _webhook(
        event_id="msg_webhook_delayed_123",
        event_type="email.delivery_delayed",
        provider_message_id="provider-message-123",
        event_created_at=signed_at - timedelta(minutes=1),
        signed_at=signed_at,
    )
    delayed = delivery.process_webhook(delayed_body, delayed_headers)

    assert delivered.matched is True
    assert duplicate.duplicate is True
    assert delayed.matched is True
    record = delivery.latest_for_workspace("workspace-a")[queued.invitation_id]
    assert record.provider_status == "delivered"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM studio_email_webhook_events"
        ).fetchone() == (2,)


def test_webhook_received_before_send_is_reconciled_after_provider_acceptance(
    tmp_path: Path,
) -> None:
    database = tmp_path / "studio.db"
    env = _env(database)
    env["RESEND_WEBHOOK_SECRET"] = WEBHOOK_SECRET
    delivery = StudioEmailDelivery.from_env(env)
    queued = delivery.queue_invitation(_issued_invitation(database))
    signed_at = datetime.now(timezone.utc)
    body, headers = _webhook(
        event_id="msg_webhook_early_123",
        event_type="email.delivered",
        provider_message_id="provider-message-123",
        event_created_at=signed_at,
        signed_at=signed_at,
    )

    assert delivery.process_webhook(body, headers).matched is False
    sent = delivery.deliver_message(queued.message_id, transport=_SuccessfulTransport())
    assert sent is not None
    assert sent.provider_status == "delivered"


def test_public_webhook_endpoint_requires_a_valid_raw_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "studio.db"
    _issued_invitation(database)
    env = _env(database)
    env["RESEND_WEBHOOK_SECRET"] = WEBHOOK_SECRET
    registry = StudioStoreRegistry(env)
    monkeypatch.setattr(studio_api, "AUTH_CONFIG", StudioAuthConfig.from_env(env))
    monkeypatch.setattr(studio_api, "EMAIL_DELIVERY", StudioEmailDelivery.from_env(env))
    monkeypatch.setattr(studio_api, "STORE_REGISTRY", registry)
    monkeypatch.setattr(studio_api, "STORE", registry.default_store)
    asyncio.run(studio_api.RATE_LIMITER.reset())
    client = TestClient(studio_api.app)
    signed_at = datetime.now(timezone.utc)
    body, headers = _webhook(
        event_id="msg_webhook_api_123",
        event_type="email.bounced",
        provider_message_id="unknown-provider-message",
        event_created_at=signed_at,
        signed_at=signed_at,
    )

    accepted = client.post(
        studio_api.RESEND_WEBHOOK_API_PATH,
        content=body,
        headers={**headers, "Content-Type": "application/json"},
    )
    rejected = client.post(
        studio_api.RESEND_WEBHOOK_API_PATH,
        content=body,
        headers={**headers, "svix-signature": "v1,invalid", "Content-Type": "application/json"},
    )
    assert accepted.status_code == 200
    assert accepted.json() == {"received": True, "duplicate": False, "matched": False}
    assert rejected.status_code == 400
    health = client.get("/api/health").json()
    assert health["email"]["webhooks"] is True
    assert "authenticated idempotent email delivery" in " ".join(health["readiness"]["completed"])
    assert "sender-domain monitoring" in " ".join(health["readiness"]["blockers"])


def test_api_queues_email_without_returning_the_secret_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "studio.db"
    env = _env(database)
    owner_key = create_studio_api_key(
        database,
        workspace_id="workspace-a",
        role="admin",
        label="Owner",
        expires_in_days=90,
        pepper=PEPPER,
    )
    registry = StudioStoreRegistry(env)
    monkeypatch.setattr(studio_api, "AUTH_CONFIG", StudioAuthConfig.from_env(env))
    monkeypatch.setattr(studio_api, "EMAIL_DELIVERY", StudioEmailDelivery.from_env(env))
    monkeypatch.setattr(studio_api, "STORE_REGISTRY", registry)
    monkeypatch.setattr(studio_api, "STORE", registry.default_store)
    monkeypatch.setattr(
        StudioEmailDelivery,
        "deliver_message",
        lambda self, message_id: None,
    )
    asyncio.run(studio_api.RATE_LIMITER.reset())
    client = TestClient(studio_api.app)
    headers = {"Authorization": f"Bearer {owner_key.api_key}"}

    health = client.get("/api/health")
    assert health.json()["email"] == {
        "enabled": True,
        "provider": "resend",
        "durable_outbox": True,
        "encrypted_payloads": True,
        "max_attempts": email_delivery.MAX_EMAIL_DELIVERY_ATTEMPTS,
        "webhooks": False,
    }
    assert "encrypted transactional invitation email" in " ".join(
        health.json()["readiness"]["completed"]
    )
    assert "bounce handling" in " ".join(health.json()["readiness"]["blockers"])

    response = client.post(
        studio_api.TEAM_INVITATIONS_API_PATH,
        headers=headers,
        json={"email": "new.person@example.com", "role": "viewer", "expires_in_days": 7},
    )
    assert response.status_code == 201
    assert response.json()["invitation_token"] is None
    assert response.json()["delivery"]["status"] == "pending"
    assert "#invite=" not in response.text

    listed = client.get(studio_api.TEAM_INVITATIONS_API_PATH, headers=headers)
    assert listed.status_code == 200
    assert listed.json()["invitations"][0]["delivery"]["status"] == "pending"

    def fail_queue(self, issued):
        raise email_delivery.StudioEmailDeliveryError("queue unavailable")

    monkeypatch.setattr(StudioEmailDelivery, "queue_invitation", fail_queue)
    fallback = client.post(
        studio_api.TEAM_INVITATIONS_API_PATH,
        headers=headers,
        json={"email": "manual@example.com", "role": "viewer", "expires_in_days": 7},
    )
    assert fallback.status_code == 201
    assert isinstance(fallback.json()["invitation_token"], str)
    assert fallback.json()["delivery"] == {
        "mode": "manual",
        "status": "not_queued",
        "attempt_count": 0,
        "last_error_code": None,
        "provider_status": None,
        "provider_event_at": None,
    }


def test_email_worker_cli_reports_an_empty_successful_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "studio.db"
    _issued_invitation(database)
    for name, value in _env(database).items():
        monkeypatch.setenv(name, value)

    assert main(["studio", "email", "deliver", "--database", str(database)]) == 0
    output = capsys.readouterr().out
    assert "Studio invitation email delivery finished" in output
    assert "Examined: 0" in output


def test_supervised_email_worker_runs_one_cycle_and_stops_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import tracebisect.cli as cli

    class FakeDelivery:
        def deliver_due(self, *, limit: int) -> StudioEmailBatchResult:
            assert limit == 12
            return StudioEmailBatchResult(examined=1, sent=1, retrying=0, failed=0)

    monkeypatch.setattr(cli, "Event", _OneCycleEvent)
    monkeypatch.setattr(cli.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(
        StudioEmailDelivery,
        "from_env",
        classmethod(lambda cls, env: FakeDelivery()),
    )

    assert (
        main(
            [
                "studio",
                "email",
                "work",
                "--database",
                str(tmp_path / "studio.db"),
                "--limit",
                "12",
                "--poll-seconds",
                "5",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Studio email worker started" in output
    assert "examined=1 accepted=1" in output
    assert "Studio email worker stopped" in output
