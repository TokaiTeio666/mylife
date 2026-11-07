"""安全验收：凭据信封加密（SEC-18）与日志脱敏（SEC-19）。"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import text

from mylife_api import repositories as repo
from mylife_api.crypto import (
    CredentialDecryptError,
    decrypt_credential,
    encrypt_credential,
)
from mylife_api.db import set_rls_context
from mylife_api.ids import uuid7
from mylife_api.settings import settings

SECRET = "sk-live-super-secret-token-1234567890"
WS = "0192a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c"


def test_envelope_roundtrip():
    blob = encrypt_credential(SECRET, workspace_id=WS, provider="llm")
    assert decrypt_credential(blob, workspace_id=WS, provider="llm") == SECRET


def test_ciphertext_contains_no_plaintext():
    blob = encrypt_credential(SECRET, workspace_id=WS, provider="llm")
    assert SECRET.encode() not in blob
    assert b"sk-live" not in blob
    # 每次加密使用独立 DEK 与 nonce
    assert blob != encrypt_credential(SECRET, workspace_id=WS, provider="llm")


def test_wrong_kek_cannot_decrypt(monkeypatch):
    blob = encrypt_credential(SECRET, workspace_id=WS, provider="llm")
    monkeypatch.setattr(settings, "secret_key", "x" * 40)
    with pytest.raises(CredentialDecryptError):
        decrypt_credential(blob, workspace_id=WS, provider="llm")


def test_aad_binds_workspace_and_provider():
    blob = encrypt_credential(SECRET, workspace_id=WS, provider="llm")
    with pytest.raises(CredentialDecryptError):
        decrypt_credential(blob, workspace_id=str(uuid7()), provider="llm")
    with pytest.raises(CredentialDecryptError):
        decrypt_credential(blob, workspace_id=WS, provider="embedding")


def test_tampered_ciphertext_is_rejected():
    blob = bytearray(encrypt_credential(SECRET, workspace_id=WS, provider="llm"))
    blob[-1] ^= 0xFF
    with pytest.raises(CredentialDecryptError):
        decrypt_credential(bytes(blob), workspace_id=WS, provider="llm")


def test_unhandled_logs_do_not_contain_exception_details(caplog):
    """SEC-19：默认不记录堆栈/SQL 参数。"""
    import asyncio

    from fastapi import FastAPI

    from mylife_api.errors import install_error_handlers

    app = FastAPI()
    install_error_handlers(app)
    handler = app.exception_handlers[Exception]

    class Boom(Exception):
        pass

    with caplog.at_level(logging.ERROR):
        asyncio.run(handler(None, Boom(SECRET)))  # type: ignore[arg-type]

    assert SECRET not in caplog.text
    record = caplog.records[-1]
    assert record.error_type == "Boom"  # type: ignore[attr-defined]
    assert not record.exc_info


@pytest.mark.db
@pytest.mark.asyncio
async def test_db_stores_only_ciphertext(admin_conn, db_session):
    """SEC-18：数据库泄露但无 KEK 时凭据不可解。"""
    from tests import helpers

    user_id = helpers.insert_user(admin_conn, email="cred@example.com")
    workspace_id = helpers.insert_workspace(admin_conn, owner_id=user_id)

    await set_rls_context(db_session, workspace_id, user_id)
    blob = encrypt_credential(SECRET, workspace_id=workspace_id, provider="llm")
    await repo.upsert_provider_credential(
        db_session,
        workspace_id=workspace_id,
        provider="llm",
        key_ciphertext=blob,
        key_version=1,
    )
    await db_session.commit()

    stored = admin_conn.execute(
        "SELECT key_ciphertext FROM provider_credentials WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchone()[0]
    stored_bytes = bytes(stored)
    assert SECRET.encode() not in stored_bytes
    assert b"sk-live" not in stored_bytes
    assert decrypt_credential(stored_bytes, workspace_id=workspace_id, provider="llm") == SECRET

    # 无 KEK（错误 KEK）→ 不可解
    original = settings.secret_key
    try:
        settings.secret_key = "y" * 40
        with pytest.raises(CredentialDecryptError):
            decrypt_credential(stored_bytes, workspace_id=workspace_id, provider="llm")
    finally:
        settings.secret_key = original


@pytest.mark.db
@pytest.mark.asyncio
async def test_credential_rls_isolated(admin_conn, db_session):
    from tests import helpers

    user_a = helpers.insert_user(admin_conn, email="cred-a@example.com")
    user_b = helpers.insert_user(admin_conn, email="cred-b@example.com")
    ws_a = helpers.insert_workspace(admin_conn, owner_id=user_a)
    ws_b = helpers.insert_workspace(admin_conn, owner_id=user_b)

    await set_rls_context(db_session, ws_b, user_b)
    await repo.upsert_provider_credential(
        db_session,
        workspace_id=ws_b,
        provider="llm",
        key_ciphertext=encrypt_credential(SECRET, workspace_id=ws_b, provider="llm"),
        key_version=1,
    )
    await db_session.commit()
    await db_session.rollback()

    await set_rls_context(db_session, ws_a, user_a)
    count = (
        await db_session.execute(text("SELECT count(*) FROM provider_credentials"))
    ).scalar_one()
    assert count == 0
    await db_session.rollback()
