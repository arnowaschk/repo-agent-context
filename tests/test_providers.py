from __future__ import annotations

from typing import Any

import pytest

import repo_agent_context.gitlab as gitlab
import repo_agent_context.providers as providers
from repo_agent_context.providers import GitLabProvider, SourceForgeProvider, get_provider


def test_get_provider_returns_expected_backend() -> None:
    assert get_provider("github").name == "github"
    assert get_provider("gitlab").name == "gitlab"
    assert get_provider("sourceforge").name == "sourceforge"

    with pytest.raises(ValueError):
        get_provider("bitbucket")


def test_sourceforge_provider_lists_tickets_and_reads_discussion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_sourceforge_json(path: str) -> object:
        calls.append(path)
        if path == "project":
            return {
                "tools": [
                    {"name": "tickets", "mount_point": "bugs", "mount_label": "Bugs"},
                    {"name": "tickets", "mount_point": "patches", "mount_label": "Patches"},
                ]
            }
        if path == "project/bugs?limit=2&page=0":
            return {
                "tickets": [
                    {"ticket_num": 12, "summary": "Open bug"},
                    {"ticket_num": 11, "summary": "Closed bug"},
                ]
            }
        if path == "project/bugs/12":
            return {
                "ticket": {
                    "ticket_num": 12,
                    "summary": "Open bug",
                    "status": "open",
                    "description": "Details",
                    "discussion_thread": {"_id": "thread-1"},
                }
            }
        if path == "project/bugs/_discuss/thread/thread-1":
            return {"posts": [{"author": "alice", "text": "I can reproduce it."}]}
        if path == "project/patches?limit=10&page=0":
            return {"tickets": [{"ticket_num": 5, "summary": "Fix scanner handling"}]}
        if path == "project/patches/5":
            return {
                "ticket": {
                    "ticket_num": 5,
                    "summary": "Fix scanner handling",
                    "status": "open",
                    "description": "Patch proposal",
                }
            }
        raise AssertionError(f"Unexpected SourceForge API path: {path}")

    monkeypatch.setattr(providers, "sourceforge_json", fake_sourceforge_json)
    provider = SourceForgeProvider()

    issues = provider.list_issues("project", "open", 2)
    issue = provider.view_issue("project", "12")

    assert [row["number"] for row in issues] == [12, 11]
    assert all(row["state"] == "UNKNOWN" for row in issues)
    assert issue["state"] == "OPEN"
    assert issue["url"] == "https://sourceforge.net/p/project/bugs/12/"
    assert issue["body"] == "Details"
    assert issue["comments"] == [
        {"author": {"login": "alice"}, "createdAt": "", "body": "I can reproduce it."}
    ]

    patches = provider.list_prs("project", "open", 10)
    patch = provider.view_pr("project", "5")
    assert [row["number"] for row in patches] == [5]
    assert patch["title"] == "Fix scanner handling"
    assert patch["body"] == "Patch proposal"
    assert patch["url"] == "https://sourceforge.net/p/project/patches/5/"
    assert provider.diff_pr("project", "5") == ""
    assert calls.count("project") == 1


def test_provider_helper_functions_cover_all_normalization_paths() -> None:
    assert providers._normalize_author(None) == {"login": "unknown"}
    assert providers._normalize_author({"login": "alice"}) == {"login": "alice"}
    assert providers._normalize_author({"username": "alice"}) == {"login": "alice"}
    assert providers._normalize_author({"name": "alice"}) == {"login": "alice"}

    assert providers._normalize_labels(None) == []
    assert providers._normalize_labels(["bug", {"name": "docs"}, {"ignored": True}]) == [
        {"name": "bug"},
        {"name": "docs"},
    ]

    assert providers._normalize_state(None) == "UNKNOWN"
    assert providers._normalize_state("opened") == "OPEN"
    assert providers._normalize_state("closed") == "CLOSED"
    assert providers._normalize_state("merged") == "MERGED"
    assert providers._normalize_state("locked") == "LOCKED"
    assert providers._normalize_state("success") == "SUCCESS"
    assert providers._normalize_state("failed") == "FAILED"
    assert providers._normalize_state("canceled") == "CANCELLED"
    assert providers._normalize_state("cancelled") == "CANCELLED"
    assert providers._normalize_state("pending") == "PENDING"
    assert providers._normalize_state("running") == "RUNNING"
    assert providers._normalize_state("created") == "CREATED"
    assert providers._normalize_state("manual") == "MANUAL"
    assert providers._normalize_state("blocked") == "BLOCKED"
    assert providers._normalize_state("scheduled") == "SCHEDULED"
    assert providers._normalize_state("preparing") == "PREPARING"
    assert providers._normalize_state("waiting_for_resource") == "WAITING_FOR_RESOURCE"
    assert providers._normalize_state("skipped") == "SKIPPED"
    assert providers._normalize_state("custom") == "CUSTOM"

    comments = providers._normalize_comments(
        [
            {
                "author": {"username": "alice"},
                "createdAt": "2026-01-01T00:00:00Z",
                "body": "visible",
            },
            {
                "author": {"username": "bot"},
                "created_at": "2026-01-02T00:00:00Z",
                "body": "system",
                "system": True,
            },
        ]
    )
    assert comments == [
        {
            "author": {"login": "alice"},
            "createdAt": "2026-01-01T00:00:00Z",
            "body": "visible",
        }
    ]

    assert providers._diff_stats("") == (0, 0)
    assert providers._diff_stats("+++ b\n--- a\n@@\n\n+one\n-two\n") == (1, 1)

    files = providers._normalize_files(
        [
            {"path": "src/a.py", "additions": 2, "deletions": 1},
            {"new_path": "src/b.py", "diff": "@@ -1 +1 @@\n-old\n+new\n"},
            {"old_path": "src/c.py", "diff": ""},
        ]
    )
    assert files == [
        {"path": "src/a.py", "additions": 2, "deletions": 1},
        {"path": "src/b.py", "additions": 1, "deletions": 1},
        {"path": "src/c.py", "additions": 0, "deletions": 0},
    ]

    commits = providers._normalize_commits(
        [
            {
                "oid": "abcdef123456",
                "short_id": "abcdef12",
                "authored_date": "2026-01-01T00:00:00Z",
                "author_name": "Alice",
                "title": "Fix bug",
                "message": "Fix bug\n\nDetails",
            },
            {
                "id": "123456789abc",
                "created_at": "2026-01-02T00:00:00Z",
                "author": "Bob",
            },
        ]
    )
    assert commits == [
        {
            "oid": "abcdef123456",
            "shortOid": "abcdef12",
            "authoredAt": "2026-01-01T00:00:00Z",
            "authorName": "Alice",
            "messageHeadline": "Fix bug",
            "message": "Fix bug\n\nDetails",
        },
        {
            "oid": "123456789abc",
            "shortOid": "123456789abc",
            "authoredAt": "2026-01-02T00:00:00Z",
            "authorName": "Bob",
            "messageHeadline": "",
            "message": "",
        },
    ]

    issue = providers._normalize_issue(
        {
            "iid": 7,
            "title": "Issue",
            "state": "opened",
            "author": {"username": "alice"},
            "labels": ["bug"],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "web_url": "https://gitlab.com/owner/repo/-/issues/7",
            "description": "Body",
        },
        comments,
    )
    assert issue == {
        "number": 7,
        "title": "Issue",
        "state": "OPEN",
        "author": {"login": "alice"},
        "labels": [{"name": "bug"}],
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-02T00:00:00Z",
        "url": "https://gitlab.com/owner/repo/-/issues/7",
        "body": "Body",
        "comments": comments,
    }

    assert providers._normalize_ci_checks(None, None) == []
    assert providers._normalize_ci_checks(
        {"id": 9, "status": "failed", "web_url": "https://gitlab.com/pipeline/9"},
        [{"name": "test", "status": "running", "web_url": "https://gitlab.com/job/1"}],
    ) == [
        {
            "name": "Pipeline 9",
            "status": "failed",
            "detailsUrl": "https://gitlab.com/pipeline/9",
        },
        {
            "name": "test",
            "status": "running",
            "detailsUrl": "https://gitlab.com/job/1",
        },
    ]

    pr = providers._normalize_pr(
        {
            "iid": 11,
            "title": "MR",
            "state": "opened",
            "author": {"username": "carol"},
            "labels": ["feature"],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-03T00:00:00Z",
            "web_url": "https://gitlab.com/owner/repo/-/merge_requests/11",
            "description": "MR body",
            "target_branch": "main",
            "source_branch": "feature",
            "has_conflicts": True,
            "draft": False,
        },
        comments=comments,
        files=files,
        commits=commits,
        status_check_rollup=[],
    )
    assert pr["number"] == 11
    assert pr["state"] == "OPEN"
    assert pr["isDraft"] is False
    assert pr["mergeable"] == "conflicting"
    assert pr["baseRefName"] == "main"
    assert pr["headRefName"] == "feature"

    wip_pr = providers._normalize_pr(
        {
            "iid": 12,
            "title": "WIP MR",
            "state": "opened",
            "author": {"username": "carol"},
            "labels": [],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-03T00:00:00Z",
            "web_url": "https://gitlab.com/owner/repo/-/merge_requests/12",
            "description": "",
            "target_branch": "main",
            "source_branch": "feature",
            "work_in_progress": True,
        },
        comments=comments,
        files=[],
        commits=[],
        status_check_rollup=[],
    )
    assert wip_pr["isDraft"] is True

    assert providers._gitlab_project_path("owner/repo") == "owner%2Frepo"


def test_gitlab_api_json_handles_paginated_json_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        providers,
        "run_glab",
        lambda args: calls.append(args)
        or '[\n  {"a":1}\n]\n[\n  {"b":2}\n]\n',
    )

    assert providers._gitlab_api_json("projects/example", paginate=True) == [{"a": 1}, {"b": 2}]
    assert calls == [["api", "projects/example", "--hostname", "gitlab.com", "--paginate", "--output", "json"]]


def test_parse_gitlab_json_output_handles_blank_and_single_values() -> None:
    assert providers._parse_gitlab_json_output("   ") == []
    assert providers._parse_gitlab_json_output('{"ok": true}') == {"ok": True}


def test_parse_gitlab_json_output_falls_back_to_stream_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        providers.json,
        "loads",
        lambda text: (_ for _ in ()).throw(providers.json.JSONDecodeError("boom", text, 0)),
    )

    assert providers._parse_gitlab_json_output('[{"a":1}]\n{"b":2}\n   ') == [{"a": 1}, {"b": 2}]


def test_parse_gitlab_json_output_returns_single_stream_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        providers.json,
        "loads",
        lambda text: (_ for _ in ()).throw(providers.json.JSONDecodeError("boom", text, 0)),
    )

    assert providers._parse_gitlab_json_output('{"a":1}\n   ') == {"a": 1}


def test_gitlab_api_json_uses_json_wrapper_for_non_paginated_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(providers, "glab_json", lambda args: calls.append(args) or {"ok": True})

    assert providers._gitlab_api_json("projects/example", paginate=False) == {"ok": True}
    assert calls == [["api", "projects/example", "--hostname", "gitlab.com", "--output", "json"]]


def test_gitlab_provider_lists_and_normalizes_items(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitLabProvider()

    issue_open = {
        "iid": 2,
        "title": "Open issue",
        "state": "opened",
        "author": {"username": "alice"},
        "labels": ["bug"],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-03T00:00:00Z",
        "web_url": "https://gitlab.com/owner/repo/-/issues/2",
        "description": "Open body",
    }
    issue_closed = {
        "iid": 1,
        "title": "Closed issue",
        "state": "closed",
        "author": {"username": "bob"},
        "labels": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "web_url": "https://gitlab.com/owner/repo/-/issues/1",
        "description": "Closed body",
    }

    mr_open = {
        "iid": 4,
        "title": "Open MR",
        "state": "opened",
        "draft": True,
        "author": {"username": "carol"},
        "labels": ["bugfix"],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-04T00:00:00Z",
        "web_url": "https://gitlab.com/owner/repo/-/merge_requests/4",
        "description": "MR body",
        "target_branch": "main",
        "source_branch": "feature",
        "detailed_merge_status": "ci_must_pass",
    }
    mr_closed = {
        "iid": 3,
        "title": "Closed MR",
        "state": "closed",
        "draft": False,
        "author": {"username": "dan"},
        "labels": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-03T00:00:00Z",
        "web_url": "https://gitlab.com/owner/repo/-/merge_requests/3",
        "description": "",
        "target_branch": "main",
        "source_branch": "fix/old",
        "detailed_merge_status": "conflict",
    }
    mr_merged = {
        "iid": 2,
        "title": "Merged MR",
        "state": "merged",
        "draft": False,
        "author": {"username": "erin"},
        "labels": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "web_url": "https://gitlab.com/owner/repo/-/merge_requests/2",
        "description": "",
        "target_branch": "main",
        "source_branch": "feature/merged",
        "detailed_merge_status": "merged",
    }

    def fake_gitlab_api_json(endpoint: str, *, paginate: bool = False) -> Any:
        if endpoint == "projects/owner%2Frepo/issues?state=opened&per_page=10":
            return [issue_open]
        if endpoint == "projects/owner%2Frepo/issues?state=closed&per_page=10":
            return [issue_closed]
        if endpoint == "projects/owner%2Frepo/merge_requests?state=opened&per_page=10":
            return [mr_open]
        if endpoint == "projects/owner%2Frepo/merge_requests?state=closed&per_page=10":
            return [mr_closed]
        if endpoint == "projects/owner%2Frepo/merge_requests?state=merged&per_page=10":
            return [mr_merged]
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(providers, "_gitlab_api_json", fake_gitlab_api_json)

    issues = provider.list_issues("owner/repo", "all", 10)
    prs = provider.list_prs("owner/repo", "all", 10)

    assert [item["number"] for item in issues] == [2, 1]
    assert issues[0]["state"] == "OPEN"
    assert issues[0]["labels"] == [{"name": "bug"}]
    assert issues[1]["state"] == "CLOSED"

    assert [item["number"] for item in prs] == [4, 3, 2]
    assert prs[0]["state"] == "OPEN"
    assert prs[0]["isDraft"] is True
    assert prs[0]["headRefName"] == "feature"
    assert prs[0]["baseRefName"] == "main"
    assert prs[1]["state"] == "CLOSED"
    assert prs[2]["state"] == "MERGED"


def test_gitlab_provider_state_filters_and_view_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GitLabProvider()
    assert provider._state_filters("open", item_kind="issues") == ["opened"]
    assert provider._state_filters("all", item_kind="issues") == ["opened", "closed"]
    assert provider._state_filters("all", item_kind="merge_requests") == ["opened", "closed", "merged"]

    calls: list[str] = []

    def fake_gitlab_api_json(endpoint: str, *, paginate: bool = False) -> Any:
        calls.append(f"{endpoint}|{paginate}")
        if endpoint == "projects/owner%2Frepo/issues/9":
            return {
                "iid": 9,
                "title": "Issue 9",
                "state": "opened",
                "author": {"username": "alice"},
                "labels": ["bug"],
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
                "web_url": "https://gitlab.com/owner/repo/-/issues/9",
                "description": "Issue body",
            }
        if endpoint == "projects/owner%2Frepo/issues/9/notes?per_page=100":
            return [
                {
                    "body": "Comment",
                    "author": {"username": "bob"},
                    "created_at": "2026-01-03T00:00:00Z",
                }
            ]
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(providers, "_gitlab_api_json", fake_gitlab_api_json)

    issue = provider.view_issue("owner/repo", "9")

    assert issue["number"] == 9
    assert issue["comments"] == [
        {
            "author": {"login": "bob"},
            "createdAt": "2026-01-03T00:00:00Z",
            "body": "Comment",
        }
    ]
    assert calls == [
        "projects/owner%2Frepo/issues/9|False",
        "projects/owner%2Frepo/issues/9/notes?per_page=100|True",
    ]


def test_gitlab_provider_view_pr_normalizes_related_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GitLabProvider()

    mr = {
        "iid": 9,
        "title": "Feature work",
        "state": "opened",
        "draft": True,
        "author": {"username": "alice"},
        "labels": ["feature"],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-05T00:00:00Z",
        "web_url": "https://gitlab.com/owner/repo/-/merge_requests/9",
        "description": "Body text",
        "target_branch": "main",
        "source_branch": "feature/test",
        "detailed_merge_status": "ci_must_pass",
    }
    notes = [
        {
            "body": "Visible note",
            "author": {"username": "bob"},
            "created_at": "2026-01-04T00:00:00Z",
            "system": False,
        },
        {
            "body": "System note",
            "author": {"username": "bot"},
            "created_at": "2026-01-03T00:00:00Z",
            "system": True,
        },
    ]
    changes = {
        "changes": [
            {
                "new_path": "src/example.py",
                "diff": "@@ -1 +1 @@\n-old\n+new\n",
            }
        ]
    }
    commits = [
        {
            "id": "abcdef123456",
            "short_id": "abcdef12",
            "title": "Fix the thing",
            "message": "Fix the thing\n\nDetails",
            "author_name": "Carol",
            "authored_date": "2026-01-02T00:00:00Z",
        }
    ]
    pipelines = [
        {
            "id": 77,
            "status": "failed",
            "web_url": "https://gitlab.com/owner/repo/-/pipelines/77",
        }
    ]
    jobs = [
        {
            "name": "test",
            "status": "failed",
            "web_url": "https://gitlab.com/owner/repo/-/jobs/991",
        }
    ]

    def fake_gitlab_api_json(endpoint: str, *, paginate: bool = False) -> Any:
        if endpoint == "projects/owner%2Frepo/merge_requests/9":
            return mr
        if endpoint == "projects/owner%2Frepo/merge_requests/9/notes?per_page=100":
            return notes
        if endpoint == "projects/owner%2Frepo/merge_requests/9/changes":
            return changes
        if endpoint == "projects/owner%2Frepo/merge_requests/9/commits":
            return commits
        if endpoint == "projects/owner%2Frepo/merge_requests/9/pipelines?per_page=100":
            return pipelines
        if endpoint == "projects/owner%2Frepo/pipelines/77/jobs?per_page=100":
            return jobs
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(providers, "_gitlab_api_json", fake_gitlab_api_json)

    full = provider.view_pr("owner/repo", "9")

    assert full["number"] == 9
    assert full["state"] == "OPEN"
    assert full["isDraft"] is True
    assert full["author"]["login"] == "alice"
    assert full["labels"] == [{"name": "feature"}]
    assert full["baseRefName"] == "main"
    assert full["headRefName"] == "feature/test"
    assert full["mergeable"] == "ci_must_pass"
    assert full["comments"] == [
        {
            "author": {"login": "bob"},
            "createdAt": "2026-01-04T00:00:00Z",
            "body": "Visible note",
        }
    ]
    assert full["files"] == [{"path": "src/example.py", "additions": 1, "deletions": 1}]
    assert full["commits"] == [
        {
            "oid": "abcdef123456",
            "shortOid": "abcdef12",
            "authoredAt": "2026-01-02T00:00:00Z",
            "authorName": "Carol",
            "messageHeadline": "Fix the thing",
            "message": "Fix the thing\n\nDetails",
        }
    ]
    assert full["statusCheckRollup"] == [
        {
            "name": "Pipeline 77",
            "status": "failed",
            "detailsUrl": "https://gitlab.com/owner/repo/-/pipelines/77",
        },
        {
            "name": "test",
            "status": "failed",
            "detailsUrl": "https://gitlab.com/owner/repo/-/jobs/991",
        },
    ]


def test_gitlab_provider_view_pr_uses_head_pipeline_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GitLabProvider()
    calls: list[str] = []

    def fake_gitlab_api_json(endpoint: str, *, paginate: bool = False) -> Any:
        calls.append(endpoint)
        if endpoint == "projects/owner%2Frepo/merge_requests/9":
            return {
                "iid": 9,
                "title": "Feature work",
                "state": "opened",
                "draft": True,
                "author": {"username": "alice"},
                "labels": ["feature"],
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-05T00:00:00Z",
                "web_url": "https://gitlab.com/owner/repo/-/merge_requests/9",
                "description": "Body text",
                "target_branch": "main",
                "source_branch": "feature/test",
                "detailed_merge_status": "ci_must_pass",
                "head_pipeline": {
                    "id": 77,
                    "project_id": 1234,
                    "status": "failed",
                    "web_url": "https://gitlab.com/fork/repo/-/pipelines/77",
                },
            }
        if endpoint == "projects/owner%2Frepo/merge_requests/9/notes?per_page=100":
            return []
        if endpoint == "projects/owner%2Frepo/merge_requests/9/changes":
            return {"changes": []}
        if endpoint == "projects/owner%2Frepo/merge_requests/9/commits":
            return []
        if endpoint == "projects/1234/pipelines/77/jobs?per_page=100":
            return [
                {
                    "name": "test",
                    "status": "failed",
                    "web_url": "https://gitlab.com/fork/repo/-/jobs/991",
                }
            ]
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(providers, "_gitlab_api_json", fake_gitlab_api_json)

    full = provider.view_pr("owner/repo", "9")

    assert full["statusCheckRollup"] == [
        {
            "name": "Pipeline 77",
            "status": "failed",
            "detailsUrl": "https://gitlab.com/fork/repo/-/pipelines/77",
        },
        {
            "name": "test",
            "status": "failed",
            "detailsUrl": "https://gitlab.com/fork/repo/-/jobs/991",
        },
    ]
    assert "projects/owner%2Frepo/merge_requests/9/pipelines?per_page=100" not in calls


def test_gitlab_provider_view_pr_without_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitLabProvider()

    def fake_gitlab_api_json(endpoint: str, *, paginate: bool = False) -> Any:
        if endpoint == "projects/owner%2Frepo/merge_requests/4":
            return {
                "iid": 4,
                "title": "No pipeline",
                "state": "closed",
                "draft": False,
                "author": {"username": "alice"},
                "labels": [],
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
                "web_url": "https://gitlab.com/owner/repo/-/merge_requests/4",
                "description": "",
                "target_branch": "main",
                "source_branch": "feature",
                "detailed_merge_status": "mergeable",
            }
        if endpoint == "projects/owner%2Frepo/merge_requests/4/notes?per_page=100":
            return []
        if endpoint == "projects/owner%2Frepo/merge_requests/4/changes":
            return {"changes": []}
        if endpoint == "projects/owner%2Frepo/merge_requests/4/commits":
            return []
        if endpoint == "projects/owner%2Frepo/merge_requests/4/pipelines?per_page=100":
            return []
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(providers, "_gitlab_api_json", fake_gitlab_api_json)

    full = provider.view_pr("owner/repo", "4")

    assert full["statusCheckRollup"] == []
    assert full["files"] == []
    assert full["commits"] == []


def test_gitlab_provider_view_pr_ignores_jobs_lookup_404(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitLabProvider()

    def fake_gitlab_api_json(endpoint: str, *, paginate: bool = False) -> Any:
        if endpoint == "projects/owner%2Frepo/merge_requests/5":
            return {
                "iid": 5,
                "title": "Jobs missing",
                "state": "opened",
                "draft": False,
                "author": {"username": "alice"},
                "labels": [],
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
                "web_url": "https://gitlab.com/owner/repo/-/merge_requests/5",
                "description": "",
                "target_branch": "main",
                "source_branch": "feature",
                "detailed_merge_status": "ci_must_pass",
            }
        if endpoint == "projects/owner%2Frepo/merge_requests/5/notes?per_page=100":
            return []
        if endpoint == "projects/owner%2Frepo/merge_requests/5/changes":
            return {"changes": []}
        if endpoint == "projects/owner%2Frepo/merge_requests/5/commits":
            return []
        if endpoint == "projects/owner%2Frepo/merge_requests/5/pipelines?per_page=100":
            return [{"id": 99, "status": "failed", "web_url": "https://gitlab.com/pipeline/99"}]
        if endpoint == "projects/owner%2Frepo/pipelines/99/jobs?per_page=100":
            raise gitlab.GitLabCliError(
                "GitLab CLI command failed:\n"
                "Command: glab api ...\n"
                "Exit code: 1\n"
                "Stderr:\n"
                "glab: 404 Not found (HTTP 404)"
            )
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(providers, "_gitlab_api_json", fake_gitlab_api_json)

    full = provider.view_pr("owner/repo", "5")

    assert full["statusCheckRollup"] == [
        {
            "name": "Pipeline 99",
            "status": "failed",
            "detailsUrl": "https://gitlab.com/pipeline/99",
        }
    ]


def test_gitlab_provider_check_methods_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitLabProvider()
    calls: list[list[str]] = []
    monkeypatch.setattr(providers, "check_glab_available", lambda: calls.append(["--version"]))
    monkeypatch.setattr(providers, "check_glab_repo_access", lambda repo: calls.append(["repo", repo]))

    provider.check_available()
    provider.check_repo_access("owner/repo")

    assert calls == [["--version"], ["repo", "owner/repo"]]


def test_gitlab_provider_diff_pr_uses_glab(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitLabProvider()
    commands: list[list[str]] = []
    monkeypatch.setattr(providers, "run_glab", lambda args: commands.append(args) or "diff text")

    assert provider.diff_pr("owner/repo", "9") == "diff text"
    assert commands == [["mr", "diff", "9", "--repo", "owner/repo"]]


def test_github_provider_methods_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = providers.GitHubProvider()
    calls: list[list[str]] = []
    monkeypatch.setattr(providers, "check_gh_available", lambda: calls.append(["--version"]))
    monkeypatch.setattr(providers, "check_gh_repo_access", lambda repo: calls.append(["repo", repo]))
    monkeypatch.setattr(providers, "gh_json", lambda args: calls.append(args) or {"ok": True})
    monkeypatch.setattr(providers, "run_gh", lambda args: calls.append(args) or "diff text")

    provider.check_available()
    provider.check_repo_access("owner/repo")
    assert provider.list_issues("owner/repo", "open", 3) == {"ok": True}
    assert provider.view_issue("owner/repo", "1") == {"ok": True}
    assert provider.list_prs("owner/repo", "all", 4) == {"ok": True}
    assert provider.view_pr("owner/repo", "2") == {"ok": True}
    assert provider.diff_pr("owner/repo", "2") == "diff text"

    assert calls[0] == ["--version"]
    assert calls[1] == ["repo", "owner/repo"]
    assert calls[2][:2] == ["issue", "list"]
    assert calls[3][:2] == ["issue", "view"]
    assert calls[4][:2] == ["pr", "list"]
    assert calls[5][:2] == ["pr", "view"]
    assert calls[6] == ["pr", "diff", "2", "--repo", "owner/repo"]
