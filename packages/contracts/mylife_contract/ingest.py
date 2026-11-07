"""导入契约（API-CONTRACTS §3.3）。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from mylife_contract.common import ApiModel, UtcDateTime


class IngestProgress(ApiModel):
    total_files: int = 0
    processed_files: int = 0
    failed_files: int = 0
    skipped_files: int = 0


class IngestJob(ApiModel):
    id: UUID
    kind: Literal["git", "zip", "sync", "resync"]
    status: Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"]
    progress: IngestProgress
    snapshot_id: UUID | None
    created_at: UtcDateTime
    started_at: UtcDateTime | None
    finished_at: UtcDateTime | None


class IngestFailure(ApiModel):
    path: str
    line: int | None
    reason_code: Literal[
        "PARSE_ERROR",
        "ENCODING_ERROR",
        "FILE_TOO_LARGE",
        "HASH_MISMATCH",
        "UNSUPPORTED_EXT",
        "PATH_OUTSIDE_ROOT",
    ]
    message: str


class IngestCounters(ApiModel):
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0


class SnapshotSummary(ApiModel):
    snapshot_id: UUID
    commit_sha: str | None
    doc_count: int
    as_of: UtcDateTime
    manifest_digest: str


class IngestJobDetail(ApiModel):
    job: IngestJob
    counters: IngestCounters
    failed_items: list[IngestFailure]
    snapshot: SnapshotSummary | None


class IngestJobList(ApiModel):
    items: list[IngestJob]
    next_cursor: str | None = None
    has_more: bool = False


class GitIngestRequest(ApiModel):
    provider: Literal["github", "gitlab"]
    repo_full_name: str = Field(min_length=3, max_length=200)
    base_branch: str = Field(min_length=1, max_length=255)
    auth_type: Literal["app", "deploy_key", "pat"]
    credential: str | None = Field(default=None, max_length=8192)
    reviews_root: str = Field(default="wiki/复盘", min_length=1, max_length=200)
    mode: Literal["managed", "vault_writeback", "separate"] = "managed"
    sync_interval_minutes: int = Field(default=0, ge=0, le=1440)
    initial_sync: bool = True

    @field_validator("repo_full_name")
    @classmethod
    def _repo(cls, v: str) -> str:
        if v.count("/") != 1 or any(not part for part in v.split("/")):
            raise ValueError("repo_full_name must be owner/repo")
        return v

    @field_validator("reviews_root")
    @classmethod
    def _reviews_root(cls, v: str) -> str:
        if ".." in v.split("/") or v.startswith("/"):
            raise ValueError("reviews_root must be a relative path without ..")
        return v


class IngestSyncRequest(ApiModel):
    connection_id: UUID
    force_refresh: bool = False


class IngestRetryRequest(ApiModel):
    only_failed: bool = True


class GitConnection(ApiModel):
    id: UUID
    provider: Literal["github", "gitlab"]
    repo_full_name: str
    base_branch: str
    mode: str
    reviews_root: str | None
    sync_interval_minutes: int
    last_sync_at: UtcDateTime | None
    last_sync_status: Literal["ok", "failed", "skipped"] | None
    credential_status: Literal["valid", "invalid", "untested"] = "untested"


class GitConnectionSummary(ApiModel):
    id: UUID
    provider: Literal["github", "gitlab"]
    repo_full_name: str
    base_branch: str
    mode: str
    reviews_root: str | None
    sync_interval_minutes: int
    last_sync_at: UtcDateTime | None
    last_sync_status: Literal["ok", "failed", "skipped"] | None
    credential_status: Literal["valid", "invalid", "untested"]


class ZipImportSummary(ApiModel):
    job_id: UUID
    status: str
    created_at: UtcDateTime
    finished_at: UtcDateTime | None


class IngestSourcesResponse(ApiModel):
    git_connections: list[GitConnectionSummary]
    zip_imports: list[ZipImportSummary]


class GitIngestResponse(ApiModel):
    job: IngestJob
    connection: GitConnection


class ZipIngestResponse(ApiModel):
    job: IngestJob
