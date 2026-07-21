"""Durable, encrypted transactional email delivery for Studio invitations."""

from __future__ import annotations

import base64
import binascii
import html
import json
import os
import re
import secrets
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import formataddr, parseaddr
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from tracebisect.studio.identity import (
    IssuedStudioInvitation,
    StudioIdentityError,
    canonical_email,
    identity_secret,
)
from tracebisect.studio.storage import (
    StudioConfigurationError,
    StudioPersistenceError,
    ensure_studio_schema,
    validate_workspace_id,
)

EMAIL_PROVIDER_ENV = "TRACEBISECT_STUDIO_EMAIL_PROVIDER"
EMAIL_FROM_ENV = "TRACEBISECT_STUDIO_EMAIL_FROM"
EMAIL_REPLY_TO_ENV = "TRACEBISECT_STUDIO_EMAIL_REPLY_TO"
PUBLIC_URL_ENV = "TRACEBISECT_STUDIO_PUBLIC_URL"
EMAIL_TIMEOUT_ENV = "TRACEBISECT_STUDIO_EMAIL_TIMEOUT_SECONDS"
RESEND_API_KEY_ENV = "RESEND_API_KEY"
RESEND_API_URL = "https://api.resend.com/emails"
DEFAULT_EMAIL_TIMEOUT_SECONDS = 10
MIN_EMAIL_TIMEOUT_SECONDS = 2
MAX_EMAIL_TIMEOUT_SECONDS = 30
MAX_EMAIL_DELIVERY_ATTEMPTS = 8
EMAIL_DELIVERY_LEASE_SECONDS = 90
EMAIL_IDEMPOTENCY_WINDOW_HOURS = 23
MAX_STORED_EMAIL_MESSAGES_PER_WORKSPACE = 1000
MAX_PROVIDER_MESSAGE_ID_LENGTH = 160
MAX_ERROR_CODE_LENGTH = 64

EmailProvider = Literal["none", "resend"]
EmailDeliveryStatus = Literal["pending", "sending", "retry", "sent", "failed"]
_DELIVERY_STATUSES = frozenset({"pending", "sending", "retry", "sent", "failed"})
_MESSAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{11}$")
_ERROR_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_RESEND_API_KEY_PATTERN = re.compile(r"^re_[A-Za-z0-9_]{12,252}$")
_CLI_SAFE_ID_FIRST_CHARACTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_ENCRYPTION_INFO = b"tracebisect-studio-email-outbox-v1"


class StudioEmailDeliveryError(RuntimeError):
    """Raised when email configuration, queueing, or delivery fails safely."""


class StudioEmailDeliveryConflict(StudioEmailDeliveryError):
    """Raised when a queue invariant prevents another message."""


class StudioEmailDeliveryNotFound(StudioEmailDeliveryError):
    """Raised when no matching invitation email can be found."""


class _TransientProviderError(StudioEmailDeliveryError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = _error_code(code)


class _PermanentProviderError(StudioEmailDeliveryError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = _error_code(code)


@dataclass(frozen=True, slots=True)
class StudioEmailConfig:
    provider: EmailProvider
    api_key: str | None = None
    from_address: str | None = None
    reply_to: str | None = None
    public_url: str | None = None
    timeout_seconds: int = DEFAULT_EMAIL_TIMEOUT_SECONDS

    @property
    def enabled(self) -> bool:
        return self.provider == "resend"


@dataclass(frozen=True, slots=True)
class StudioEmailMessage:
    to: str
    from_address: str
    reply_to: str | None
    subject: str
    html: str
    text: str


@dataclass(frozen=True, slots=True)
class StudioEmailDeliveryRecord:
    message_id: str
    invitation_id: str
    workspace_id: str
    recipient_email: str
    status: EmailDeliveryStatus
    attempt_count: int
    created_at: str
    available_at: str
    sent_at: str | None
    failed_at: str | None
    last_error_code: str | None


@dataclass(frozen=True, slots=True)
class StudioEmailBatchResult:
    examined: int
    sent: int
    retrying: int
    failed: int


@dataclass(frozen=True, slots=True)
class _ClaimedEmail:
    record: StudioEmailDeliveryRecord
    payload_ciphertext: str
    lease_token: str


class StudioEmailTransport(Protocol):
    def send(self, message: StudioEmailMessage, *, idempotency_key: str) -> str: ...


class ResendEmailTransport:
    """Small Resend HTTP client with bounded responses and secret-safe errors."""

    def __init__(self, *, api_key: str, timeout_seconds: int) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def send(self, message: StudioEmailMessage, *, idempotency_key: str) -> str:
        payload: dict[str, object] = {
            "from": message.from_address,
            "to": [message.to],
            "subject": message.subject,
            "html": message.html,
            "text": message.text,
            "tags": [{"name": "kind", "value": "workspace_invite"}],
        }
        if message.reply_to is not None:
            payload["reply_to"] = message.reply_to
        request = Request(
            RESEND_API_URL,
            data=json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
                "User-Agent": "TraceBisect-Studio/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                status = int(response.status)
                body = response.read(16_384)
        except HTTPError as exc:
            if exc.code in {408, 409, 429} or exc.code >= 500:
                raise _TransientProviderError(f"resend_http_{exc.code}") from None
            raise _PermanentProviderError(f"resend_http_{exc.code}") from None
        except (TimeoutError, URLError, OSError):
            raise _TransientProviderError("resend_network") from None
        if status not in {200, 201}:
            if status in {408, 409, 429} or status >= 500:
                raise _TransientProviderError(f"resend_http_{status}")
            raise _PermanentProviderError(f"resend_http_{status}")
        try:
            parsed = json.loads(body)
            provider_message_id = parsed["id"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise _TransientProviderError("resend_invalid_response") from exc
        if (
            not isinstance(provider_message_id, str)
            or not provider_message_id
            or len(provider_message_id) > MAX_PROVIDER_MESSAGE_ID_LENGTH
        ):
            raise _TransientProviderError("resend_invalid_response")
        return provider_message_id


@dataclass(frozen=True, slots=True)
class StudioEmailDelivery:
    """Configuration and durable outbox operations for invitation emails."""

    config: StudioEmailConfig
    database_path: Path | None = None
    _identity_secret: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> StudioEmailDelivery:
        values = os.environ if env is None else env
        provider = values.get(EMAIL_PROVIDER_ENV, "none").strip().lower()
        if provider == "none":
            raw_database_path = values.get("TRACEBISECT_STUDIO_SQLITE_PATH", "").strip()
            database_path = (
                Path(raw_database_path).expanduser().resolve()
                if values.get("TRACEBISECT_STUDIO_STORAGE", "memory").strip().lower()
                == "sqlite"
                and raw_database_path
                else None
            )
            return cls(
                config=StudioEmailConfig(provider="none"),
                database_path=database_path,
            )
        if provider != "resend":
            raise StudioConfigurationError(f"{EMAIL_PROVIDER_ENV} must be 'none' or 'resend'")
        raw_database_path = values.get("TRACEBISECT_STUDIO_SQLITE_PATH", "").strip()
        if values.get("TRACEBISECT_STUDIO_STORAGE", "memory").strip().lower() != "sqlite":
            raise StudioConfigurationError(
                f"{EMAIL_PROVIDER_ENV}=resend requires TRACEBISECT_STUDIO_STORAGE=sqlite"
            )
        if not raw_database_path:
            raise StudioConfigurationError(
                f"{EMAIL_PROVIDER_ENV}=resend requires TRACEBISECT_STUDIO_SQLITE_PATH"
            )
        secret = identity_secret(values)
        api_key = values.get(RESEND_API_KEY_ENV, "")
        if _RESEND_API_KEY_PATTERN.fullmatch(api_key) is None:
            raise StudioConfigurationError(
                f"{RESEND_API_KEY_ENV} must be an ASCII Resend key beginning with re_"
            )
        from_address = _mailbox(values.get(EMAIL_FROM_ENV, ""), label=EMAIL_FROM_ENV)
        raw_reply_to = values.get(EMAIL_REPLY_TO_ENV, "").strip()
        reply_to = _mailbox(raw_reply_to, label=EMAIL_REPLY_TO_ENV) if raw_reply_to else None
        public_url = _public_url(values.get(PUBLIC_URL_ENV, ""))
        timeout_seconds = _timeout_seconds(values.get(EMAIL_TIMEOUT_ENV, ""))
        return cls(
            config=StudioEmailConfig(
                provider="resend",
                api_key=api_key,
                from_address=from_address,
                reply_to=reply_to,
                public_url=public_url,
                timeout_seconds=timeout_seconds,
            ),
            database_path=Path(raw_database_path).expanduser().resolve(),
            _identity_secret=secret,
        )

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def runtime_status(self) -> dict[str, str | bool | int]:
        return {
            "enabled": self.enabled,
            "provider": self.config.provider,
            "durable_outbox": self.enabled,
            "encrypted_payloads": self.enabled,
            "max_attempts": MAX_EMAIL_DELIVERY_ATTEMPTS if self.enabled else 0,
        }

    def queue_invitation(
        self,
        issued: IssuedStudioInvitation,
        *,
        now: datetime | None = None,
    ) -> StudioEmailDeliveryRecord:
        database, secret = self._material()
        current = _utc_now(now)
        message_id = _new_message_id()
        message = _invitation_message(issued, config=self.config)
        ciphertext = _encrypt_message(
            message,
            message_id=message_id,
            identity_secret_value=secret,
        )
        workspace_id = validate_workspace_id(issued.record.workspace_id)
        try:
            with sqlite3.connect(database, timeout=5) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 5000")
                ensure_studio_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                _ensure_invitation_is_pending(
                    connection,
                    invitation_id=issued.record.invitation_id,
                    workspace_id=workspace_id,
                    current=current,
                )
                _prepare_outbox_capacity(
                    connection,
                    workspace_id=workspace_id,
                    invitation_id=issued.record.invitation_id,
                )
                timestamp = _timestamp(current)
                connection.execute(
                    """
                    INSERT INTO studio_email_outbox (
                        message_id, invitation_id, workspace_id, recipient_email,
                        payload_ciphertext, status, attempt_count, created_at,
                        available_at, lease_expires_at, lease_token, sent_at, failed_at,
                        provider_message_id, last_error_code
                    ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL)
                    """,
                    (
                        message_id,
                        issued.record.invitation_id,
                        workspace_id,
                        canonical_email(issued.record.email),
                        ciphertext,
                        timestamp,
                        timestamp,
                    ),
                )
        except StudioEmailDeliveryError:
            raise
        except (OSError, sqlite3.DatabaseError, StudioIdentityError, StudioPersistenceError) as exc:
            raise StudioEmailDeliveryError("could not queue the invitation email") from exc
        return StudioEmailDeliveryRecord(
            message_id=message_id,
            invitation_id=issued.record.invitation_id,
            workspace_id=workspace_id,
            recipient_email=canonical_email(issued.record.email),
            status="pending",
            attempt_count=0,
            created_at=_timestamp(current),
            available_at=_timestamp(current),
            sent_at=None,
            failed_at=None,
            last_error_code=None,
        )

    def requeue_invitation(
        self,
        *,
        workspace_id: str,
        invitation_id: str,
        now: datetime | None = None,
    ) -> StudioEmailDeliveryRecord:
        database, secret = self._material()
        workspace = validate_workspace_id(workspace_id)
        current = _utc_now(now)
        message_id = _new_message_id()
        try:
            with sqlite3.connect(database, timeout=5) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 5000")
                ensure_studio_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                _ensure_invitation_is_pending(
                    connection,
                    invitation_id=invitation_id,
                    workspace_id=workspace,
                    current=current,
                )
                source = connection.execute(
                    """
                    SELECT message_id, payload_ciphertext, recipient_email
                    FROM studio_email_outbox
                    WHERE workspace_id = ? AND invitation_id = ?
                    ORDER BY created_at DESC, message_id DESC LIMIT 1
                    """,
                    (workspace, invitation_id),
                ).fetchone()
                if source is None:
                    raise StudioEmailDeliveryNotFound(
                        "this invitation was created for manual delivery"
                    )
                _prepare_outbox_capacity(
                    connection,
                    workspace_id=workspace,
                    invitation_id=invitation_id,
                )
                original = _decrypt_message(
                    str(source[1]),
                    message_id=str(source[0]),
                    identity_secret_value=secret,
                )
                ciphertext = _encrypt_message(
                    original,
                    message_id=message_id,
                    identity_secret_value=secret,
                )
                timestamp = _timestamp(current)
                connection.execute(
                    """
                    INSERT INTO studio_email_outbox (
                        message_id, invitation_id, workspace_id, recipient_email,
                        payload_ciphertext, status, attempt_count, created_at,
                        available_at, lease_expires_at, lease_token, sent_at, failed_at,
                        provider_message_id, last_error_code
                    ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL)
                    """,
                    (
                        message_id,
                        invitation_id,
                        workspace,
                        canonical_email(str(source[2])),
                        ciphertext,
                        timestamp,
                        timestamp,
                    ),
                )
        except StudioEmailDeliveryError:
            raise
        except (OSError, sqlite3.DatabaseError, StudioIdentityError, StudioPersistenceError) as exc:
            raise StudioEmailDeliveryError("could not queue another invitation email") from exc
        return StudioEmailDeliveryRecord(
            message_id=message_id,
            invitation_id=invitation_id,
            workspace_id=workspace,
            recipient_email=canonical_email(str(source[2])),
            status="pending",
            attempt_count=0,
            created_at=_timestamp(current),
            available_at=_timestamp(current),
            sent_at=None,
            failed_at=None,
            last_error_code=None,
        )

    def latest_for_workspace(
        self,
        workspace_id: str,
    ) -> dict[str, StudioEmailDeliveryRecord]:
        if self.database_path is None:
            return {}
        database = self.database_path
        workspace = validate_workspace_id(workspace_id)
        records: dict[str, StudioEmailDeliveryRecord] = {}
        try:
            with sqlite3.connect(
                f"{database.as_uri()}?mode=ro",
                uri=True,
                timeout=5,
            ) as connection:
                for row in connection.execute(
                    """
                    SELECT message_id, invitation_id, workspace_id, recipient_email,
                           status, attempt_count, created_at, available_at,
                           sent_at, failed_at, last_error_code
                    FROM studio_email_outbox
                    WHERE workspace_id = ?
                    ORDER BY created_at DESC, message_id DESC
                    """,
                    (workspace,),
                ):
                    record = _record_from_row(row)
                    records.setdefault(record.invitation_id, record)
        except (
            OSError,
            sqlite3.DatabaseError,
            StudioEmailDeliveryError,
            StudioIdentityError,
            ValueError,
        ) as exc:
            raise StudioEmailDeliveryError("could not load invitation delivery status") from exc
        return records

    def deliver_message(
        self,
        message_id: str,
        *,
        transport: StudioEmailTransport | None = None,
        now: datetime | None = None,
    ) -> StudioEmailDeliveryRecord | None:
        database, secret = self._material()
        current = _utc_now(now)
        claimed = _claim_message(database, message_id=message_id, current=current)
        if claimed is None:
            return None
        if isinstance(claimed, StudioEmailDeliveryRecord):
            return claimed
        sender = transport or self._transport()
        try:
            message = _decrypt_message(
                claimed.payload_ciphertext,
                message_id=claimed.record.message_id,
                identity_secret_value=secret,
            )
            provider_message_id = sender.send(
                message,
                idempotency_key=f"tracebisect-invite-{claimed.record.message_id}",
            )
        except _PermanentProviderError as exc:
            return _mark_failed(
                database,
                claimed.record,
                lease_token=claimed.lease_token,
                error_code=exc.code,
                current=current,
            )
        except _TransientProviderError as exc:
            return _mark_retry_or_failed(
                database,
                claimed.record,
                lease_token=claimed.lease_token,
                error_code=exc.code,
                current=current,
            )
        except StudioEmailDeliveryError:
            return _mark_failed(
                database,
                claimed.record,
                lease_token=claimed.lease_token,
                error_code="payload_invalid",
                current=current,
            )
        return _mark_sent(
            database,
            claimed.record,
            lease_token=claimed.lease_token,
            provider_message_id=provider_message_id,
            current=current,
        )

    def deliver_due(
        self,
        *,
        limit: int = 20,
        transport: StudioEmailTransport | None = None,
        now: datetime | None = None,
    ) -> StudioEmailBatchResult:
        if not 1 <= limit <= 100:
            raise StudioEmailDeliveryError("email delivery limit must be between 1 and 100")
        database, _secret = self._material()
        current = _utc_now(now)
        try:
            with sqlite3.connect(database, timeout=5) as connection:
                ensure_studio_schema(connection)
                rows = connection.execute(
                    """
                    SELECT message_id FROM studio_email_outbox
                    WHERE (
                        status IN ('pending', 'retry') AND available_at <= ?
                    ) OR (
                        status = 'sending' AND lease_expires_at IS NOT NULL
                        AND lease_expires_at <= ?
                    )
                    ORDER BY available_at, created_at, message_id
                    LIMIT ?
                    """,
                    (_timestamp(current), _timestamp(current), limit),
                ).fetchall()
        except (OSError, sqlite3.DatabaseError, StudioPersistenceError) as exc:
            raise StudioEmailDeliveryError("could not load due invitation emails") from exc
        results = [
            self.deliver_message(str(row[0]), transport=transport, now=current)
            for row in rows
        ]
        completed = [record for record in results if record is not None]
        return StudioEmailBatchResult(
            examined=len(rows),
            sent=sum(record.status == "sent" for record in completed),
            retrying=sum(record.status == "retry" for record in completed),
            failed=sum(record.status == "failed" for record in completed),
        )

    def _material(self) -> tuple[Path, str]:
        if not self.enabled or self.database_path is None or self._identity_secret is None:
            raise StudioEmailDeliveryError("automatic invitation email is not configured")
        return self.database_path, self._identity_secret

    def _transport(self) -> StudioEmailTransport:
        if self.config.api_key is None:
            raise StudioEmailDeliveryError("automatic invitation email is not configured")
        return ResendEmailTransport(
            api_key=self.config.api_key,
            timeout_seconds=self.config.timeout_seconds,
        )


def _invitation_message(
    issued: IssuedStudioInvitation,
    *,
    config: StudioEmailConfig,
) -> StudioEmailMessage:
    if config.public_url is None or config.from_address is None:
        raise StudioEmailDeliveryError("automatic invitation email is not configured")
    workspace = validate_workspace_id(issued.record.workspace_id)
    recipient = canonical_email(issued.record.email)
    invitation_url = (
        f"{config.public_url}/#invite={quote(issued.invitation_token, safe='')}"
    )
    workspace_html = html.escape(workspace)
    role_html = html.escape(issued.record.role)
    url_html = html.escape(invitation_url, quote=True)
    expires_html = html.escape(issued.record.expires_at)
    subject = f"You're invited to {workspace} in TraceBisect"
    text = (
        f"You have been invited to the {workspace} workspace in TraceBisect Studio "
        f"with {issued.record.role} access.\n\n"
        f"Accept the invitation: {invitation_url}\n\n"
        f"This private link expires at {issued.record.expires_at}. If you were not "
        "expecting this invitation, you can ignore this email."
    )
    email_html = f"""<!doctype html>
<html lang="en">
<body style="margin:0;background:#f4f7f5;color:#12211b;font-family:Arial,sans-serif">
<div style="display:none;max-height:0;overflow:hidden">
Join {workspace_html} in TraceBisect Studio
</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0">
<tr><td style="padding:32px 16px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"
 style="max-width:560px;margin:auto;background:#fff;border:1px solid #dce7e1;
 border-radius:14px">
<tr><td style="padding:32px">
<p style="margin:0 0 20px;color:#2c6975;font-size:13px;font-weight:700">
TRACEBISECT STUDIO
</p>
<h1 style="margin:0 0 14px;font-size:26px;line-height:1.2">Join {workspace_html}</h1>
<p style="margin:0 0 22px;color:#52635b;line-height:1.6">
You were invited with <strong>{role_html}</strong> access. Create or connect your account
to review AI-agent changes with the team.
</p>
<p style="margin:0 0 24px">
<a href="{url_html}" style="display:inline-block;padding:12px 18px;border-radius:8px;
 background:#2c6975;color:#fff;text-decoration:none;font-weight:700">
Accept invitation
</a>
</p>
<p style="margin:0 0 8px;color:#6c7d75;font-size:12px;line-height:1.5">
This private link expires at {expires_html}. Do not forward it.
</p>
<p style="margin:0;color:#6c7d75;font-size:12px;line-height:1.5">
If you were not expecting this invitation, ignore this email.
</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""
    return StudioEmailMessage(
        to=recipient,
        from_address=config.from_address,
        reply_to=config.reply_to,
        subject=subject,
        html=email_html,
        text=text,
    )


def _claim_message(
    database: Path,
    *,
    message_id: str,
    current: datetime,
) -> _ClaimedEmail | StudioEmailDeliveryRecord | None:
    _message_id(message_id)
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_studio_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT outbox.message_id, outbox.invitation_id, outbox.workspace_id,
                       outbox.recipient_email,
                       outbox.status, attempt_count, outbox.created_at, available_at,
                       sent_at, failed_at, last_error_code, payload_ciphertext,
                       lease_expires_at, invitation.expires_at,
                       invitation.accepted_at, invitation.revoked_at
                FROM studio_email_outbox AS outbox
                JOIN studio_invitations AS invitation USING (invitation_id)
                WHERE outbox.message_id = ?
                """,
                (message_id,),
            ).fetchone()
            if row is None:
                return None
            status = str(row[4])
            due = status in {"pending", "retry"} and str(row[7]) <= _timestamp(current)
            expired_lease = (
                status == "sending"
                and row[12] is not None
                and str(row[12]) <= _timestamp(current)
            )
            if not due and not expired_lease:
                return None
            if (
                row[14] is not None
                or row[15] is not None
                or _parse_timestamp(str(row[13])) <= current
            ):
                failed_at = _timestamp(current)
                connection.execute(
                    """
                    UPDATE studio_email_outbox
                    SET status = 'failed', failed_at = ?, lease_expires_at = NULL,
                        lease_token = NULL, last_error_code = 'invitation_inactive'
                    WHERE message_id = ?
                    """,
                    (failed_at, message_id),
                )
                return _replace_record(
                    _record_from_row(row[:11]),
                    status="failed",
                    failed_at=failed_at,
                    last_error_code="invitation_inactive",
                )
            if (
                status in {"retry", "sending"}
                and int(row[5]) > 0
                and _parse_timestamp(str(row[6]))
                <= current - timedelta(hours=EMAIL_IDEMPOTENCY_WINDOW_HOURS)
            ):
                failed_at = _timestamp(current)
                connection.execute(
                    """
                    UPDATE studio_email_outbox
                    SET status = 'failed', failed_at = ?, lease_expires_at = NULL,
                        lease_token = NULL, last_error_code = 'idempotency_window_expired'
                    WHERE message_id = ?
                    """,
                    (failed_at, message_id),
                )
                return _replace_record(
                    _record_from_row(row[:11]),
                    status="failed",
                    failed_at=failed_at,
                    last_error_code="idempotency_window_expired",
                )
            attempt_count = int(row[5]) + 1
            lease_expires = current + timedelta(seconds=EMAIL_DELIVERY_LEASE_SECONDS)
            lease_token = secrets.token_urlsafe(18)
            connection.execute(
                """
                UPDATE studio_email_outbox
                SET status = 'sending', attempt_count = ?, lease_expires_at = ?,
                    lease_token = ?, last_error_code = NULL
                WHERE message_id = ?
                """,
                (attempt_count, _timestamp(lease_expires), lease_token, message_id),
            )
            record = _record_from_row((*row[:4], "sending", attempt_count, *row[6:11]))
            return _ClaimedEmail(
                record=record,
                payload_ciphertext=str(row[11]),
                lease_token=lease_token,
            )
    except StudioEmailDeliveryError:
        raise
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError, ValueError) as exc:
        raise StudioEmailDeliveryError("could not claim the invitation email") from exc


def _mark_sent(
    database: Path,
    record: StudioEmailDeliveryRecord,
    *,
    lease_token: str,
    provider_message_id: str,
    current: datetime,
) -> StudioEmailDeliveryRecord:
    sent_at = _timestamp(current)
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            cursor = connection.execute(
                """
                UPDATE studio_email_outbox
                SET status = 'sent', sent_at = ?, failed_at = NULL,
                    lease_expires_at = NULL, lease_token = NULL,
                    provider_message_id = ?,
                    last_error_code = NULL
                WHERE message_id = ? AND status = 'sending' AND lease_token = ?
                """,
                (
                    sent_at,
                    provider_message_id[:MAX_PROVIDER_MESSAGE_ID_LENGTH],
                    record.message_id,
                    lease_token,
                ),
            )
            _require_active_lease(cursor)
    except (OSError, sqlite3.DatabaseError) as exc:
        raise StudioEmailDeliveryError("could not finalize the sent invitation email") from exc
    return _replace_record(record, status="sent", sent_at=sent_at)


def _mark_retry_or_failed(
    database: Path,
    record: StudioEmailDeliveryRecord,
    *,
    lease_token: str,
    error_code: str,
    current: datetime,
) -> StudioEmailDeliveryRecord:
    if record.attempt_count >= MAX_EMAIL_DELIVERY_ATTEMPTS:
        return _mark_failed(
            database,
            record,
            lease_token=lease_token,
            error_code=error_code,
            current=current,
        )
    delay_seconds = min(3600, 30 * (2 ** max(0, record.attempt_count - 1)))
    available_at = _timestamp(current + timedelta(seconds=delay_seconds))
    safe_error_code = _error_code(error_code)
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            cursor = connection.execute(
                """
                UPDATE studio_email_outbox
                SET status = 'retry', available_at = ?, lease_expires_at = NULL,
                    lease_token = NULL, last_error_code = ?
                WHERE message_id = ? AND status = 'sending' AND lease_token = ?
                """,
                (available_at, safe_error_code, record.message_id, lease_token),
            )
            _require_active_lease(cursor)
    except (OSError, sqlite3.DatabaseError) as exc:
        raise StudioEmailDeliveryError("could not schedule the invitation email retry") from exc
    return _replace_record(
        record,
        status="retry",
        available_at=available_at,
        last_error_code=safe_error_code,
    )


def _mark_failed(
    database: Path,
    record: StudioEmailDeliveryRecord,
    *,
    lease_token: str,
    error_code: str,
    current: datetime,
) -> StudioEmailDeliveryRecord:
    failed_at = _timestamp(current)
    safe_error_code = _error_code(error_code)
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            cursor = connection.execute(
                """
                UPDATE studio_email_outbox
                SET status = 'failed', failed_at = ?, lease_expires_at = NULL,
                    lease_token = NULL, last_error_code = ?
                WHERE message_id = ? AND status = 'sending' AND lease_token = ?
                """,
                (failed_at, safe_error_code, record.message_id, lease_token),
            )
            _require_active_lease(cursor)
    except (OSError, sqlite3.DatabaseError) as exc:
        raise StudioEmailDeliveryError("could not finalize the failed invitation email") from exc
    return _replace_record(
        record,
        status="failed",
        failed_at=failed_at,
        last_error_code=safe_error_code,
    )


def _replace_record(
    record: StudioEmailDeliveryRecord,
    *,
    status: EmailDeliveryStatus,
    available_at: str | None = None,
    sent_at: str | None = None,
    failed_at: str | None = None,
    last_error_code: str | None = None,
) -> StudioEmailDeliveryRecord:
    return StudioEmailDeliveryRecord(
        message_id=record.message_id,
        invitation_id=record.invitation_id,
        workspace_id=record.workspace_id,
        recipient_email=record.recipient_email,
        status=status,
        attempt_count=record.attempt_count,
        created_at=record.created_at,
        available_at=available_at or record.available_at,
        sent_at=sent_at,
        failed_at=failed_at,
        last_error_code=last_error_code,
    )


def _require_active_lease(cursor: sqlite3.Cursor) -> None:
    if cursor.rowcount != 1:
        raise StudioEmailDeliveryConflict("the email delivery lease is no longer active")


def _prepare_outbox_capacity(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    invitation_id: str,
) -> None:
    active = connection.execute(
        """
        SELECT 1 FROM studio_email_outbox
        WHERE invitation_id = ? AND status IN ('pending', 'sending', 'retry')
        LIMIT 1
        """,
        (invitation_id,),
    ).fetchone()
    if active is not None:
        raise StudioEmailDeliveryConflict("an invitation email is already queued")
    oldest_final = connection.execute(
        """
        SELECT message_id FROM studio_email_outbox
        WHERE workspace_id = ? AND status IN ('sent', 'failed')
        ORDER BY created_at DESC, message_id DESC
        LIMIT -1 OFFSET ?
        """,
        (workspace_id, MAX_STORED_EMAIL_MESSAGES_PER_WORKSPACE - 1),
    ).fetchall()
    connection.executemany(
        "DELETE FROM studio_email_outbox WHERE message_id = ?",
        oldest_final,
    )
    count = int(
        connection.execute(
            "SELECT COUNT(*) FROM studio_email_outbox WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()[0]
    )
    if count >= MAX_STORED_EMAIL_MESSAGES_PER_WORKSPACE:
        raise StudioEmailDeliveryConflict("workspace email outbox is at its safe limit")


def _ensure_invitation_is_pending(
    connection: sqlite3.Connection,
    *,
    invitation_id: str,
    workspace_id: str,
    current: datetime,
) -> None:
    row = connection.execute(
        """
        SELECT expires_at, accepted_at, revoked_at
        FROM studio_invitations
        WHERE invitation_id = ? AND workspace_id = ?
        """,
        (invitation_id, workspace_id),
    ).fetchone()
    if (
        row is None
        or row[1] is not None
        or row[2] is not None
        or _parse_timestamp(str(row[0])) <= current
    ):
        raise StudioEmailDeliveryConflict("invitation is not active")


def _encrypt_message(
    message: StudioEmailMessage,
    *,
    message_id: str,
    identity_secret_value: str,
) -> str:
    payload = json.dumps(
        {
            "to": message.to,
            "from": message.from_address,
            "reply_to": message.reply_to,
            "subject": message.subject,
            "html": message.html,
            "text": message.text,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(_encryption_key(identity_secret_value)).encrypt(
        nonce,
        payload,
        _associated_data(message_id),
    )
    return f"v1.{_b64(nonce)}.{_b64(ciphertext)}"


def _decrypt_message(
    value: str,
    *,
    message_id: str,
    identity_secret_value: str,
) -> StudioEmailMessage:
    try:
        version, nonce_value, ciphertext_value = value.split(".", 2)
        if version != "v1":
            raise ValueError("unsupported payload version")
        plaintext = AESGCM(_encryption_key(identity_secret_value)).decrypt(
            _unb64(nonce_value),
            _unb64(ciphertext_value),
            _associated_data(message_id),
        )
        parsed = json.loads(plaintext)
        if not isinstance(parsed, dict):
            raise ValueError("invalid email payload")
        reply_to = parsed.get("reply_to")
        if reply_to is not None and not isinstance(reply_to, str):
            raise ValueError("invalid reply-to")
        fields = [parsed.get(name) for name in ("to", "from", "subject", "html", "text")]
        if any(not isinstance(field, str) or not field for field in fields):
            raise ValueError("invalid email field")
        return StudioEmailMessage(
            to=cast(str, fields[0]),
            from_address=cast(str, fields[1]),
            reply_to=reply_to,
            subject=cast(str, fields[2]),
            html=cast(str, fields[3]),
            text=cast(str, fields[4]),
        )
    except (binascii.Error, InvalidTag, KeyError, TypeError, ValueError) as exc:
        raise StudioEmailDeliveryError("email payload could not be decrypted") from exc


def _encryption_key(identity_secret_value: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_ENCRYPTION_INFO,
    ).derive(identity_secret_value.encode())


def _associated_data(message_id: str) -> bytes:
    return f"tracebisect-email-outbox:v1:{_message_id(message_id)}".encode()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(f"{value}{'=' * (-len(value) % 4)}")


def _record_from_row(row: tuple[object, ...]) -> StudioEmailDeliveryRecord:
    status = str(row[4])
    if status not in _DELIVERY_STATUSES:
        raise StudioEmailDeliveryError("stored email delivery status is invalid")
    validated_status = cast(EmailDeliveryStatus, status)
    return StudioEmailDeliveryRecord(
        message_id=_message_id(str(row[0])),
        invitation_id=str(row[1]),
        workspace_id=validate_workspace_id(str(row[2])),
        recipient_email=canonical_email(str(row[3])),
        status=validated_status,
        attempt_count=int(str(row[5])),
        created_at=str(row[6]),
        available_at=str(row[7]),
        sent_at=None if row[8] is None else str(row[8]),
        failed_at=None if row[9] is None else str(row[9]),
        last_error_code=None if row[10] is None else _error_code(str(row[10])),
    )


def _mailbox(value: str, *, label: str) -> str:
    normalized = value.strip()
    display_name, address = parseaddr(normalized)
    if not normalized or not address:
        raise StudioConfigurationError(f"{label} must contain a valid email address")
    try:
        validated_address = canonical_email(address)
    except StudioIdentityError as exc:
        raise StudioConfigurationError(f"{label} must contain a valid email address") from exc
    if display_name:
        if any(character in display_name for character in "\r\n<>"):
            raise StudioConfigurationError(f"{label} contains unsupported characters")
        return formataddr((display_name.strip(), validated_address))
    return validated_address


def _public_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise StudioConfigurationError(
            f"{PUBLIC_URL_ENV} must be an HTTP(S) URL without credentials, query, or fragment"
        )
    loopback_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and parsed.hostname not in loopback_hosts:
        raise StudioConfigurationError(
            f"{PUBLIC_URL_ENV} must use HTTPS outside local development"
        )
    return normalized


def _timeout_seconds(value: str) -> int:
    if not value:
        return DEFAULT_EMAIL_TIMEOUT_SECONDS
    try:
        timeout = int(value)
    except ValueError as exc:
        raise StudioConfigurationError(
            f"{EMAIL_TIMEOUT_ENV} must be an integer from "
            f"{MIN_EMAIL_TIMEOUT_SECONDS} to {MAX_EMAIL_TIMEOUT_SECONDS}"
        ) from exc
    if not MIN_EMAIL_TIMEOUT_SECONDS <= timeout <= MAX_EMAIL_TIMEOUT_SECONDS:
        raise StudioConfigurationError(
            f"{EMAIL_TIMEOUT_ENV} must be from "
            f"{MIN_EMAIL_TIMEOUT_SECONDS} to {MAX_EMAIL_TIMEOUT_SECONDS}"
        )
    return timeout


def _new_message_id() -> str:
    raw = secrets.token_urlsafe(9)
    value = f"{secrets.choice(_CLI_SAFE_ID_FIRST_CHARACTERS)}{raw[1:]}"
    return _message_id(value)


def _message_id(value: str) -> str:
    if _MESSAGE_ID_PATTERN.fullmatch(value) is None:
        raise StudioEmailDeliveryError("email message ID is invalid")
    return value


def _error_code(value: str) -> str:
    normalized = value.strip().lower()[:MAX_ERROR_CODE_LENGTH]
    if _ERROR_CODE_PATTERN.fullmatch(normalized) is None:
        return "delivery_error"
    return normalized


def _utc_now(value: datetime | None) -> datetime:
    current = datetime.now(timezone.utc) if value is None else value
    if current.tzinfo is None:
        raise StudioEmailDeliveryError("email delivery timestamps must include a timezone")
    return current.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise StudioEmailDeliveryError("email delivery timestamp has no timezone")
    return parsed.astimezone(timezone.utc)
