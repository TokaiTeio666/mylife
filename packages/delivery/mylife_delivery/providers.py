"""Git 提供方 PR/连接测试（ARCHITECTURE §8.5；AC-52/AC-67）。

只依赖标准库，避免把 HTTP 客户端版本钉进交付包。同步实现，由 API 侧
`asyncio.to_thread` 调用。错误统一为 `ProviderError`，由服务层映射为稳定错误码。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("mylife.delivery.provider")

DEFAULT_GITHUB_API = "https://api.github.com"
DEFAULT_GITLAB_API = "https://gitlab.com/api/v4"


class ProviderError(Exception):
    def __init__(self, code: str, reason: str = "", status: int = 0) -> None:
        self.code = code
        self.reason = reason
        self.status = status
        super().__init__(f"{code}:{reason}")


@dataclass(slots=True)
class PullRequest:
    number: int
    url: str
    state: str = "open"


@dataclass(slots=True)
class TestResult:
    ok: bool
    reason: str = ""


class BaseProvider:
    def __init__(
        self,
        *,
        repo_full_name: str,
        token: str,
        api_base_url: str | None = None,
        timeout_seconds: int = 20,
    ) -> None:
        self.repo_full_name = repo_full_name
        self.token = token
        self.api_base_url = api_base_url
        self.timeout_seconds = timeout_seconds

    # ------------------------------------------------------------ HTTP
    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        url = f"{self.base}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/json")
        request.add_header("Content-Type", "application/json")
        for key, value in self.headers().items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
                return response.status, json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body) if body else None
            except json.JSONDecodeError:
                parsed = None
            return exc.code, parsed
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError("DELIVERY_GIT_UNAVAILABLE", str(exc)) from exc

    @property
    def base(self) -> str:  # pragma: no cover - 子类覆盖
        raise NotImplementedError

    def headers(self) -> dict[str, str]:  # pragma: no cover - 子类覆盖
        raise NotImplementedError

    def test_connection(self) -> TestResult:  # pragma: no cover - 子类覆盖
        raise NotImplementedError

    def find_open_pr(self, head_branch: str) -> PullRequest | None:  # pragma: no cover
        raise NotImplementedError

    def create_or_update_pr(
        self,
        *,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
    ) -> PullRequest:  # pragma: no cover
        raise NotImplementedError

    def merge_pr(self, number: int) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError


class GitHubProvider(BaseProvider):
    @property
    def base(self) -> str:
        return (self.api_base_url or DEFAULT_GITHUB_API).rstrip("/")

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def test_connection(self) -> TestResult:
        status, payload = self._request("GET", f"/repos/{self.repo_full_name}")
        if status in {401, 403}:
            return TestResult(False, "AUTH_FAILED")
        if status == 404:
            return TestResult(False, "REPO_UNREACHABLE")
        if status >= 300 or not isinstance(payload, dict):
            return TestResult(False, f"HTTP_{status}")
        permissions = payload.get("permissions") or {}
        if not permissions.get("push"):
            return TestResult(False, "MISSING_CONTENTS_WRITE")
        return TestResult(True)

    def _owner(self) -> str:
        return self.repo_full_name.split("/", 1)[0]

    def find_open_pr(self, head_branch: str) -> PullRequest | None:
        query = urllib.parse.urlencode(
            {"head": f"{self._owner()}:{head_branch}", "state": "open"}
        )
        status, payload = self._request(
            "GET", f"/repos/{self.repo_full_name}/pulls?{query}"
        )
        if status >= 300 or not isinstance(payload, list) or not payload:
            return None
        item = payload[0]
        return PullRequest(
            number=int(item.get("number")),
            url=str(item.get("html_url") or ""),
            state=str(item.get("state") or "open"),
        )

    def create_or_update_pr(
        self,
        *,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
    ) -> PullRequest:
        existing = self.find_open_pr(head_branch)
        if existing is not None:
            status, payload = self._request(
                "PATCH",
                f"/repos/{self.repo_full_name}/pulls/{existing.number}",
                {"title": title, "body": body},
            )
            if status >= 300:
                raise ProviderError("DELIVERY_PR_CONFLICT", f"HTTP_{status}", status)
            return existing
        status, payload = self._request(
            "POST",
            f"/repos/{self.repo_full_name}/pulls",
            {"title": title, "body": body, "head": head_branch, "base": base_branch},
        )
        if status >= 300 or not isinstance(payload, dict):
            reason = "PR_ALREADY_EXISTS" if status == 422 else f"HTTP_{status}"
            raise ProviderError("DELIVERY_PR_CONFLICT", reason, status)
        return PullRequest(
            number=int(payload.get("number")),
            url=str(payload.get("html_url") or ""),
            state=str(payload.get("state") or "open"),
        )

    def merge_pr(self, number: int) -> dict[str, Any]:
        status, payload = self._request(
            "PUT",
            f"/repos/{self.repo_full_name}/pulls/{number}/merge",
            {"merge_method": "squash"},
        )
        if status >= 300 or not isinstance(payload, dict):
            raise ProviderError("DELIVERY_PR_CONFLICT", f"HTTP_{status}", status)
        if not payload.get("merged"):
            raise ProviderError(
                "DELIVERY_PR_CONFLICT", str(payload.get("message") or "not merged"), status
            )
        return {
            "merged": True,
            "merge_commit": payload.get("sha"),
            "number": number,
        }


class GitLabProvider(BaseProvider):
    @property
    def base(self) -> str:
        return (self.api_base_url or DEFAULT_GITLAB_API).rstrip("/")

    def headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self.token}

    def _project(self) -> str:
        return urllib.parse.quote(self.repo_full_name, safe="")

    def test_connection(self) -> TestResult:
        status, payload = self._request("GET", f"/projects/{self._project()}")
        if status in {401, 403}:
            return TestResult(False, "AUTH_FAILED")
        if status == 404:
            return TestResult(False, "REPO_UNREACHABLE")
        if status >= 300 or not isinstance(payload, dict):
            return TestResult(False, f"HTTP_{status}")
        access = (payload.get("permissions") or {}).get("project_access") or {}
        if int(access.get("access_level") or 0) < 30:
            return TestResult(False, "MISSING_CONTENTS_WRITE")
        return TestResult(True)

    def find_open_pr(self, head_branch: str) -> PullRequest | None:
        query = urllib.parse.urlencode(
            {"source_branch": head_branch, "state": "opened"}
        )
        status, payload = self._request(
            "GET", f"/projects/{self._project()}/merge_requests?{query}"
        )
        if status >= 300 or not isinstance(payload, list) or not payload:
            return None
        item = payload[0]
        return PullRequest(
            number=int(item.get("iid")),
            url=str(item.get("web_url") or ""),
            state=str(item.get("state") or "opened"),
        )

    def create_or_update_pr(
        self,
        *,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
    ) -> PullRequest:
        existing = self.find_open_pr(head_branch)
        if existing is not None:
            status, _ = self._request(
                "PUT",
                f"/projects/{self._project()}/merge_requests/{existing.number}",
                {"title": title, "description": body},
            )
            if status >= 300:
                raise ProviderError("DELIVERY_PR_CONFLICT", f"HTTP_{status}", status)
            return existing
        status, payload = self._request(
            "POST",
            f"/projects/{self._project()}/merge_requests",
            {
                "title": title,
                "description": body,
                "source_branch": head_branch,
                "target_branch": base_branch,
            },
        )
        if status >= 300 or not isinstance(payload, dict):
            raise ProviderError("DELIVERY_PR_CONFLICT", f"HTTP_{status}", status)
        return PullRequest(
            number=int(payload.get("iid")),
            url=str(payload.get("web_url") or ""),
            state=str(payload.get("state") or "opened"),
        )

    def merge_pr(self, number: int) -> dict[str, Any]:
        status, payload = self._request(
            "PUT", f"/projects/{self._project()}/merge_requests/{number}/merge"
        )
        if status >= 300 or not isinstance(payload, dict):
            raise ProviderError("DELIVERY_PR_CONFLICT", f"HTTP_{status}", status)
        if payload.get("state") != "merged":
            raise ProviderError("DELIVERY_PR_CONFLICT", str(payload.get("state")), status)
        return {
            "merged": True,
            "merge_commit": payload.get("merge_commit_sha") or payload.get("sha"),
            "number": number,
        }


def provider_for(
    *,
    provider: str,
    repo_full_name: str,
    token: str,
    api_base_url: str | None = None,
) -> BaseProvider:
    if provider == "github":
        return GitHubProvider(
            repo_full_name=repo_full_name, token=token, api_base_url=api_base_url
        )
    if provider == "gitlab":
        return GitLabProvider(
            repo_full_name=repo_full_name, token=token, api_base_url=api_base_url
        )
    raise ProviderError("GIT_PROVIDER_UNSUPPORTED", provider)


__all__ = [
    "BaseProvider",
    "GitHubProvider",
    "GitLabProvider",
    "ProviderError",
    "PullRequest",
    "TestResult",
    "provider_for",
]
