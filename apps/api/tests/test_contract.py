"""契约冻结测试（无需数据库）。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from mylife_contract.auth import RegisterRequest, SessionInfo, User
from mylife_contract.common import ErrorEnvelope, Page, UtcDateTime
from mylife_contract.constants import DAILY_SECTIONS, SENTINELS
from mylife_contract.enums import RunState
from mylife_contract.errors import ERROR_SPECS, ErrorCode
from pydantic import ValidationError


def test_every_error_code_has_spec():
    assert set(ERROR_SPECS) == set(ErrorCode)


@pytest.mark.parametrize(
    ("code", "status", "retryable"),
    [
        (ErrorCode.VALIDATION_ERROR, 422, False),
        (ErrorCode.NOT_FOUND, 404, False),
        (ErrorCode.AUTH_INVALID_CREDENTIALS, 401, False),
        (ErrorCode.AUTH_ACCOUNT_LOCKED, 429, True),
        (ErrorCode.AUTH_INVITE_REQUIRED, 403, False),
        (ErrorCode.AUTH_INVITE_USED, 400, False),
        (ErrorCode.AUTH_CSRF_INVALID, 403, True),
        (ErrorCode.ACTIVE_RUN_EXISTS, 409, False),
        (ErrorCode.BUDGET_EXCEEDED, 429, False),
        (ErrorCode.INTERNAL_ERROR, 500, True),
        (ErrorCode.SECURITY_ABORT, 403, False),
        (ErrorCode.ADMIN_REQUIRED, 403, False),
    ],
)
def test_error_spec_matches_document(code, status, retryable):
    spec = ERROR_SPECS[code]
    assert spec.http_status == status
    assert spec.retryable is retryable
    assert spec.message


def test_workspace_id_in_body_is_rejected():
    with pytest.raises(ValidationError) as excinfo:
        RegisterRequest(
            email="a@example.com",
            password="12345678",
            display_name="A",
            invite_code="INV-ABCDEFGH",
            workspace_id="00000000-0000-0000-0000-000000000000",
        )
    assert "WORKSPACE_ID_NOT_ALLOWED" in str(excinfo.value)


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        RegisterRequest(
            email="a@example.com",
            password="12345678",
            display_name="A",
            invite_code="INV-ABCDEFGH",
            extra_field=True,
        )


def test_email_is_normalized_lowercase():
    request = RegisterRequest(
        email="  Alice@Example.COM ",
        password="12345678",
        display_name=" Alice ",
        invite_code="INV-ABCDEFGH",
    )
    assert request.email == "alice@example.com"
    assert request.display_name == "Alice"


def test_timestamp_serializes_as_utc_z():
    model = User(
        id="0192a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b",
        email="a@example.com",
        display_name="A",
        status="active",
        created_at=datetime(2026, 11, 6, 1, 30, tzinfo=UTC),
    )
    dumped = model.model_dump(mode="json")
    assert dumped["created_at"] == "2026-11-06T01:30:00Z"


def test_nullable_fields_are_explicit():
    session = SessionInfo(
        expires_at=datetime(2026, 12, 6, 1, 0, tzinfo=UTC),
        active_workspace_id="0192a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c",
        csrf_token="token",
    )
    assert set(session.model_dump()) == {
        "expires_at",
        "active_workspace_id",
        "csrf_token",
    }


def test_error_envelope_shape():
    envelope = ErrorEnvelope.model_validate(
        {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "请求参数不合法",
                "details": None,
                "retryable": False,
            },
            "request_id": "req_01J9Z3K4M5N6P7Q8R9S0T1V2W3",
        }
    )
    assert envelope.error.code == "VALIDATION_ERROR"
    assert envelope.error.details is None


def test_page_is_generic():
    page = Page[int](items=[1, 2], next_cursor=None, has_more=False)
    assert page.items == [1, 2]
    assert page.next_cursor is None


def test_run_state_enum_is_complete():
    expected = {
        "created",
        "snapshotting",
        "planning",
        "retrieving",
        "drafting",
        "reviewing",
        "revising",
        "human_gate",
        "delivering",
        "succeeded",
        "retry_wait",
        "needs_human",
        "security_abort",
        "failed",
        "cancelled",
    }
    assert {state.value for state in RunState} == expected


def test_daily_sections_and_sentinels():
    assert [row[1] for row in DAILY_SECTIONS] == [
        "today_tasks",
        "learning_summary",
        "journal_events",
        "mistakes",
        "methods",
        "tomorrow_actions",
        "one_line_summary",
    ]
    assert [row[0] for row in DAILY_SECTIONS] == list(range(1, 8))
    for _, key, _, _ in DAILY_SECTIONS:
        assert key in SENTINELS


def test_review_section_schemas_cover_all_sections():
    from mylife_contract.constants import (
        DAILY_SECTIONS,
        SENTINELS,
        WEEKLY_SECTIONS,
        YEARLY_SECTIONS,
    )
    from mylife_contract.review import ITEM_SCHEMAS, SECTION_SCHEMAS

    expected = {
        "daily": [row[1] for row in DAILY_SECTIONS],
        "weekly": [row[1] for row in WEEKLY_SECTIONS],
        "yearly": [row[1] for row in YEARLY_SECTIONS],
    }
    for kind, keys in expected.items():
        assert list(SECTION_SCHEMAS[kind]) == keys
        for key in keys:
            assert key in SENTINELS
            # extra="forbid" 生效：未知键必须被拒绝
            with pytest.raises(ValidationError):
                SECTION_SCHEMAS[kind][key].model_validate({"unknown_field": 1})
    assert set(ITEM_SCHEMAS) == {"error", "method", "action"}


def test_budget_defaults_match_decision_5b():
    from mylife_contract.budget import Budget, BudgetUsage
    from mylife_contract.constants import DEFAULT_BUDGET_BY_KIND, REVISION_LIMIT

    assert DEFAULT_BUDGET_BY_KIND["daily"] == {
        "max_input_tokens": 100_000,
        "max_output_tokens": 20_000,
    }
    assert DEFAULT_BUDGET_BY_KIND["weekly"]["max_input_tokens"] == 300_000
    assert DEFAULT_BUDGET_BY_KIND["yearly"]["max_output_tokens"] == 200_000
    assert REVISION_LIMIT == {"daily": 2, "weekly": 2, "yearly": 3}

    budget = Budget(max_input_tokens=10, max_output_tokens=5)
    assert budget.warn_ratio == 0.8 and budget.hard_stop_ratio == 1.0
    usage = BudgetUsage()
    assert usage.state == "ok" and usage.ratio == 0.0
    with pytest.raises(ValidationError):
        Budget(max_input_tokens=10, max_output_tokens=5, warn_ratio=1.0)


def test_tool_contracts_extended():
    from mylife_contract.tools import (
        CreateReviewConfirmArgs,
        CreateReviewPreview,
        EstimatedTokens,
        TaskItem,
    )

    confirm = CreateReviewConfirmArgs(confirmation_token="v1.1.abc")
    assert confirm.confirmation_token == "v1.1.abc"
    preview = CreateReviewPreview(
        kind="daily",
        period_key="2026-10-27",
        title="t",
        path="p",
        estimated_evidence_count=1,
        estimated_tokens=EstimatedTokens(input=10, output=2),
    )
    assert preview.estimated_tokens.input == 10
    item = TaskItem(
        id="0192a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b",
        text="x",
        status="open",
        first_seen="2026-10-27",
        last_seen="2026-10-27",
        done_date=None,
        source_path=None,
        line_number=None,
    )
    assert item.occurrence_count == 0 and item.days_open is None


def test_utc_datetime_annotation():
    from pydantic import TypeAdapter

    adapter = TypeAdapter(UtcDateTime)
    assert adapter.dump_json(datetime(2026, 11, 6, 1, 0, tzinfo=UTC)).decode() == (
        '"2026-11-06T01:00:00Z"'
    )
