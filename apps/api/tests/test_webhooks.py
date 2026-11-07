"""SEC-16 / SEC-17：webhook 验签与幂等。"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest

from tests import helpers
from tests.helpers import API

pytestmark = pytest.mark.db

SECRET = "test-webhook-secret"
REPO = "acme/notes"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _headers(*, signature: str | None, delivery_id: str, event: str = "ping") -> dict:
    headers = {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": event,
        "Content-Type": "application/json",
    }
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return headers


async def _seed_connection(admin_conn, *, workspace_id: str) -> None:
    from mylife_api.crypto import encrypt_credential

    blob = encrypt_credential(SECRET, workspace_id=workspace_id, provider="git-webhook")
    admin_conn.execute(
        "INSERT INTO git_connections "
        "(id, workspace_id, role, provider, repo_full_name, base_branch, auth_type, "
        " webhook_secret_ciphertext) "
        "VALUES (%s, %s, 'output', 'github', %s, 'main', 'pat', %s)",
        (uuid.uuid4(), workspace_id, REPO, blob),
    )


@pytest.mark.asyncio
async def test_signature_invalid_no_side_effect(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="hook1@example.com", files={}
    )
    await _seed_connection(admin_conn, workspace_id=workspace_id)

    body = json.dumps({"zen": "hi", "repository": {"full_name": REPO}}).encode()
    response = await client.post(
        f"{API}/webhooks/git/github",
        content=body,
        headers=_headers(signature="sha256=deadbeef", delivery_id="d-1"),
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "WEBHOOK_SIGNATURE_INVALID"

    assert (
        admin_conn.execute(
            "SELECT count(*) FROM delivery_events WHERE workspace_id = %s",
            (workspace_id,),
        ).fetchone()[0]
        == 0
    )
    assert (
        admin_conn.execute(
            "SELECT count(*) FROM audit_logs WHERE workspace_id = %s "
            "AND action = 'webhook.rejected'",
            (workspace_id,),
        ).fetchone()[0]
        == 1
    )


@pytest.mark.asyncio
async def test_missing_signature_rejected(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="hook2@example.com", files={}
    )
    await _seed_connection(admin_conn, workspace_id=workspace_id)
    body = json.dumps({"repository": {"full_name": REPO}}).encode()
    response = await client.post(
        f"{API}/webhooks/git/github",
        content=body,
        headers=_headers(signature=None, delivery_id="d-2"),
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_replay_is_idempotent(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="hook3@example.com", files={}
    )
    await _seed_connection(admin_conn, workspace_id=workspace_id)
    body = json.dumps({"zen": "hi", "repository": {"full_name": REPO}}).encode()
    headers = _headers(signature=_sign(body), delivery_id="d-3")

    first = await client.post(f"{API}/webhooks/git/github", content=body, headers=headers)
    assert first.status_code == 200
    assert first.json()["received"] is True
    assert first.json()["handled"] is True  # ping

    second = await client.post(f"{API}/webhooks/git/github", content=body, headers=headers)
    assert second.status_code == 200
    assert second.json()["handled"] is False

    assert (
        admin_conn.execute(
            "SELECT count(*) FROM delivery_events WHERE workspace_id = %s",
            (workspace_id,),
        ).fetchone()[0]
        == 1
    )


@pytest.mark.asyncio
async def test_merged_pr_is_ignored_in_phase3(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="hook4@example.com", files={}
    )
    await _seed_connection(admin_conn, workspace_id=workspace_id)
    payload = {
        "action": "closed",
        "pull_request": {"number": 1, "merged": True},
        "repository": {"full_name": REPO},
    }
    body = json.dumps(payload).encode()
    response = await client.post(
        f"{API}/webhooks/git/github",
        content=body,
        headers=_headers(
            signature=_sign(body), delivery_id="d-4", event="pull_request"
        ),
    )
    assert response.status_code == 200
    assert response.json()["handled"] is False
    status = admin_conn.execute(
        "SELECT status FROM delivery_events WHERE provider_delivery_id = 'd-4'"
    ).fetchone()[0]
    assert status == "ignored"


@pytest.mark.asyncio
async def test_merged_pr_writes_back_delivery_record(client, admin_conn):
    workspace_id, user_id = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="hook5@example.com", files={}
    )
    await _seed_connection(admin_conn, workspace_id=workspace_id)
    review_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    admin_conn.execute(
        "INSERT INTO reviews (id, workspace_id, kind, period_key, title, content, version) "
        "VALUES (%s, %s, 'daily', '2026-10-27', 'x', 'x', 1)",
        (review_id, workspace_id),
    )
    admin_conn.execute(
        "INSERT INTO delivery_records "
        "(id, workspace_id, review_id, version, target, provider, branch, status) "
        "VALUES (%s, %s, %s, 1, 'git', 'github', 'review/daily/2026-10-27', 'pr_open')",
        (delivery_id, workspace_id, review_id),
    )
    payload = {
        "action": "closed",
        "pull_request": {
            "number": 3,
            "merged": True,
            "merge_commit_sha": "a" * 40,
            "head": {"ref": "review/daily/2026-10-27"},
        },
        "repository": {"full_name": REPO},
    }
    body = json.dumps(payload).encode()
    response = await client.post(
        f"{API}/webhooks/git/github",
        content=body,
        headers=_headers(
            signature=_sign(body), delivery_id="d-5", event="pull_request"
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["handled"] is True
    row = admin_conn.execute(
        "SELECT status, merged_commit FROM delivery_records WHERE id = %s",
        (delivery_id,),
    ).fetchone()
    assert row == ("merged", "a" * 40)


@pytest.mark.asyncio
async def test_unsupported_provider(client, admin_conn):
    response = await client.post(
        f"{API}/webhooks/git/bitbucket",
        content=b"{}",
        headers={"X-Hub-Signature-256": "sha256=x"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "GIT_PROVIDER_UNSUPPORTED"


@pytest.mark.asyncio
async def test_gitlab_token_verification(client, admin_conn):
    workspace_id, _ = await helpers.prepare_workspace_with_docs(
        client, admin_conn, email="hook5@example.com", files={}
    )
    admin_conn.execute(
        "INSERT INTO git_connections "
        "(id, workspace_id, role, provider, repo_full_name, base_branch, auth_type, "
        " webhook_secret_ciphertext) "
        "VALUES (%s, %s, 'output', 'gitlab', %s, 'main', 'pat', %s)",
        (
            uuid.uuid4(),
            workspace_id,
            REPO,
            __import__("mylife_api.crypto", fromlist=["x"]).encrypt_credential(
                SECRET, workspace_id=workspace_id, provider="git-webhook"
            ),
        ),
    )
    body = json.dumps({"repository": {"full_name": REPO}}).encode()
    bad = await client.post(
        f"{API}/webhooks/git/gitlab",
        content=body,
        headers={"X-Gitlab-Token": "wrong", "X-Gitlab-Event-UUID": "g-1"},
    )
    assert bad.status_code == 401

    good = await client.post(
        f"{API}/webhooks/git/gitlab",
        content=body,
        headers={"X-Gitlab-Token": SECRET, "X-Gitlab-Event-UUID": "g-2"},
    )
    assert good.status_code == 200
