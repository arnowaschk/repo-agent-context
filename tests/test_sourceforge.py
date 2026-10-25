from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

import repo_agent_context.sourceforge as sourceforge
from repo_agent_context.sourceforge import SourceForgeApiError


def test_sourceforge_json_requests_and_decodes_response(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, int]] = []

    def fake_urlopen(request: object, *, timeout: int) -> BytesIO:
        calls.append((request, timeout))
        return BytesIO(b'{"status":"active"}')

    monkeypatch.setattr(sourceforge, "urlopen", fake_urlopen)

    result = sourceforge.sourceforge_json("/project")

    assert result == {"status": "active"}
    assert len(calls) == 1
    request, timeout = calls[0]
    assert request.get_full_url() == "https://sourceforge.net/rest/p/project"
    assert request.get_method() == "GET"
    assert request.get_header("Accept") == "application/json"
    assert request.get_header("User-agent") == "repo-agent-context"
    assert timeout == 30


@pytest.mark.parametrize(
    "error",
    [
        HTTPError("https://sourceforge.net/rest/p/project", 404, "Not Found", {}, None),
        URLError("connection failed"),
        TimeoutError("request timed out"),
        json.JSONDecodeError("invalid JSON", "{", 1),
    ],
    ids=["http", "connection", "timeout", "invalid-json"],
)
def test_sourceforge_json_wraps_request_and_decode_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    def failing_urlopen(request: object, *, timeout: int) -> BytesIO:
        raise error

    monkeypatch.setattr(sourceforge, "urlopen", failing_urlopen)

    with pytest.raises(SourceForgeApiError, match="SourceForge API request failed for project") as exc_info:
        sourceforge.sourceforge_json("project")

    assert exc_info.value.__cause__ is error
