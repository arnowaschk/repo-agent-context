import json
from pathlib import Path

import pytest

from repo_agent_context.render import (
    author_login,
    check_name,
    check_state,
    ci_status_counts,
    ci_summary,
    extract_references_from_comments,
    extract_references_from_text,
    is_attention_check,
    label_names,
    render_branches_ahead,
    render_ci_status,
    render_comments,
    render_issue,
    render_issues_index,
    render_pr,
    render_prs_index,
    render_relations,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_render_relations_detects_pr_referencing_issue() -> None:
    issues = [
        {
            "number": 10,
            "title": "Bug",
            "body": "",
            "comments": [],
        }
    ]

    prs = [
        {
            "number": 20,
            "title": "Fix bug",
            "body": "Fixes #10",
            "comments": [],
        }
    ]

    rendered = render_relations(issues, prs)

    assert "PR #20 references Issue #10" in rendered


def test_render_relations_detects_comment_reference() -> None:
    issues = [
        {
            "number": 10,
            "title": "Bug",
            "body": "",
            "comments": [],
        }
    ]

    prs = [
        {
            "number": 20,
            "title": "Fix bug",
            "body": "",
            "comments": [
                {
                    "body": "This probably relates to #10.",
                    "author": {"login": "alice"},
                }
            ],
        }
    ]

    rendered = render_relations(issues, prs)

    assert "PR #20 references Issue #10" in rendered
    assert "(comment)" in rendered


def test_render_pr_includes_ci_checks_requiring_attention() -> None:
    pr = {
        "number": 20,
        "title": "Fix bug",
        "state": "OPEN",
        "isDraft": False,
        "author": {"login": "alice"},
        "labels": [],
        "baseRefName": "master",
        "headRefName": "fix-bug",
        "mergeable": "MERGEABLE",
        "reviewDecision": None,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-02T00:00:00Z",
        "url": "https://github.com/example/repo/pull/20",
        "body": "",
        "files": [],
        "commits": [],
        "comments": [],
        "statusCheckRollup": [
            {"name": "tests", "conclusion": "FAILURE", "detailsUrl": "https://ci.example/tests"},
            {"name": "lint", "conclusion": "SUCCESS"},
        ],
    }

    rendered = render_pr(pr)

    assert "## CI status" in rendered
    assert "failure: 1" in rendered
    assert "success: 1" in rendered
    assert "tests [failure]" in rendered
    assert "lint [success]" not in rendered


def test_render_prs_index_includes_ci_summary() -> None:
    rendered = render_prs_index(
        [
            {
                "number": 20,
                "title": "Fix bug",
                "state": "OPEN",
                "isDraft": False,
                "reviewDecision": None,
                "mergeable": "MERGEABLE",
                "updatedAt": "2026-01-02T00:00:00Z",
                "statusCheckRollup": [{"name": "tests", "conclusion": "FAILURE"}],
            }
        ]
    )

    assert "[ci: failure: 1]" in rendered


def test_render_branches_ahead_lists_commits() -> None:
    rendered = render_branches_ahead(
        {
            "repo": "example/repo",
            "remote": "upstream",
            "baseBranch": "master",
            "branches": [
                {
                    "name": "feature",
                    "aheadBy": 1,
                    "behindBy": 0,
                    "commits": [
                        {
                            "shortOid": "abc123",
                            "subject": "Add feature",
                            "authoredAt": "2026-01-01T00:00:00+00:00",
                            "authorName": "Alice",
                        }
                    ],
                }
            ],
        }
    )

    assert "## feature [ahead: 1] [behind: 0]" in rendered
    assert "`abc123` Add feature" in rendered


def test_extract_references_handles_empty_text_and_multiple_forms() -> None:
    assert extract_references_from_text("") == set()
    assert extract_references_from_text("Fixes #1, refs #2 and unrelated #3") == {1, 2, 3}


def test_extract_references_from_comments_handles_missing_bodies() -> None:
    assert extract_references_from_comments([{}, {"body": "Closes #4"}]) == {4}


def test_extract_references_ignores_fenced_code_blocks() -> None:
    assert extract_references_from_text("```python\nFixes #7\n```") == set()


def test_author_and_labels_fallback_for_missing_or_empty_values() -> None:
    assert author_login({}) == "unknown"
    assert author_login({"author": {"login": "alice"}}) == "alice"
    assert label_names({"labels": [{"name": "bug"}, {}, {"name": ""}]}) == "bug"


def test_ci_helpers_use_fallback_fields_and_unknown_defaults() -> None:
    checks = [
        {"context": "legacy", "state": "PENDING"},
        {"workflowName": "workflow", "status": "QUEUED"},
        {"title": "title", "conclusion": "NEUTRAL"},
        {},
    ]

    assert [check_name(check) for check in checks] == ["legacy", "workflow", "title", "unknown"]
    assert [check_state(check) for check in checks] == ["PENDING", "QUEUED", "NEUTRAL", "UNKNOWN"]
    assert ci_status_counts(checks) == {"PENDING": 1, "QUEUED": 1, "NEUTRAL": 1, "UNKNOWN": 1}
    assert ci_summary([]) == "no checks"
    assert ci_summary(
        [
            {"conclusion": "SUCCESS"},
            {"status": "PENDING"},
            {"conclusion": "FAILURE"},
        ]
    ) == "failure: 1, pending: 1, success: 1"
    assert is_attention_check({"conclusion": "SUCCESS"}) is False
    assert is_attention_check({"conclusion": "SKIPPED"}) is False
    assert is_attention_check({"conclusion": "FAILURE"}) is True


def test_render_ci_status_handles_empty_success_and_target_url() -> None:
    assert render_ci_status([]) == "_No CI status checks listed._"
    assert "No failing or pending checks." in render_ci_status(
        [{"name": "lint", "conclusion": "SUCCESS"}]
    )

    rendered = render_ci_status(
        [{"context": "build", "status": "PENDING", "targetUrl": "https://ci"}]
    )

    assert "build [pending] (https://ci)" in rendered


def test_render_ci_status_orders_attention_checks_by_severity() -> None:
    rendered = render_ci_status(
        [
            {"name": "queued", "status": "PENDING"},
            {"name": "tests", "conclusion": "FAILURE"},
        ]
    )

    assert rendered.index("tests [failure]") < rendered.index("queued [pending]")


def test_render_comments_includes_author_created_and_body() -> None:
    rendered = render_comments(
        [{"author": {"login": "alice"}, "createdAt": "2026-01-01", "body": "hello"}]
    )

    assert "Comment by alice at 2026-01-01" in rendered
    assert "hello" in rendered


def test_render_pr_uses_message_fallback_and_empty_sections() -> None:
    rendered = render_pr(
        {
            "number": 3,
            "title": "Fallback PR",
            "commits": [{"oid": "1234567890abcdef", "message": "fallback message"}],
        }
    )

    assert "`1234567890ab` fallback message" in rendered
    assert "_No files listed._" in rendered
    assert "_No comments._" in rendered


def test_render_indexes_sort_by_updated_at_descending() -> None:
    issues = [
        {"number": 1, "title": "old", "updatedAt": "2026-01-01", "labels": [], "state": "OPEN"},
        {"number": 2, "title": "new", "updatedAt": "2026-01-02", "labels": [], "state": "OPEN"},
    ]
    prs = [
        {
            "number": 1,
            "title": "old",
            "updatedAt": "2026-01-01",
            "state": "OPEN",
            "isDraft": False,
            "reviewDecision": None,
            "mergeable": "UNKNOWN",
            "statusCheckRollup": [],
        },
        {
            "number": 2,
            "title": "new",
            "updatedAt": "2026-01-02",
            "state": "OPEN",
            "isDraft": False,
            "reviewDecision": None,
            "mergeable": "UNKNOWN",
            "statusCheckRollup": [],
        },
    ]

    issue_lines = render_issues_index(issues).splitlines()
    pr_lines = render_prs_index(prs).splitlines()

    assert "#2: new" in issue_lines[2]
    assert "#2: new" in pr_lines[2]


def test_render_relations_covers_all_relation_sections_and_self_reference_skips() -> None:
    issues = [
        {"number": 1, "title": "one", "body": "Refs #1 and #2 and #10", "comments": []},
        {"number": 2, "title": "two", "body": "", "comments": [{"body": "See #1 and #20"}]},
    ]
    prs = [
        {"number": 10, "title": "ten", "body": "Refs #10 and #20", "comments": []},
        {"number": 20, "title": "twenty", "body": "", "comments": [{"body": "Refs #1 and #10"}]},
    ]

    rendered = render_relations(issues, prs)

    assert "Issue #1 references Issue #2 (body)" in rendered
    assert "Issue #2 references Issue #1 (comment)" in rendered
    assert "Issue #1 references PR #10 (body)" in rendered
    assert "Issue #2 references PR #20 (comment)" in rendered
    assert "PR #10 references PR #20 (body)" in rendered
    assert "PR #20 references PR #10 (comment)" in rendered
    assert "PR #20 references Issue #1 (comment)" in rendered
    assert "PR #10 references PR #10" not in rendered


def test_sourceforge_issue_index_qualifies_tracker_identity() -> None:
    issues = json.loads((FIXTURES / "sourceforge_tracker_collisions.json").read_text(encoding="utf-8"))

    rendered = render_issues_index(issues)

    assert "- bugs/71: Bug ticket 71" in rendered
    assert "- support-requests/71: Support ticket 71" in rendered


def test_sourceforge_relation_marks_duplicate_ticket_numbers_ambiguous() -> None:
    issues = json.loads((FIXTURES / "sourceforge_tracker_collisions.json").read_text(encoding="utf-8"))

    rendered = render_relations(issues, [])

    assert (
        "Issue bugs/446 references Issue #71 "
        "(ambiguous: bugs/71, support-requests/71) (body/ambiguous)"
    ) in rendered
    assert "Issue bugs/446 references Issue bugs/71 (body)" not in rendered


def test_render_relations_outputs_empty_section_messages() -> None:
    rendered = render_relations([], [])

    assert "_No PR-to-issue references detected._" in rendered
    assert "_No issue-to-PR references detected._" in rendered
    assert "_No issue-to-issue references detected._" in rendered
    assert "_No PR-to-PR references detected._" in rendered


def test_render_relations_covers_unverified_and_self_reference_comment_paths() -> None:
    rendered = render_relations(
        [
            {
                "number": 1,
                "title": "Issue",
                "body": "Refs #999",
                "comments": [{"body": "Refs #1 and #998"}],
            }
        ],
        [
            {
                "number": 10,
                "title": "PR",
                "body": "",
                "comments": [{"body": "Refs #10 and #997"}],
            }
        ],
    )

    assert "Issue #1 references Issue #999 (body/unverified)" in rendered
    assert "Issue #1 references Issue #998 (comment/unverified)" in rendered
    assert "PR #10 references Issue #997 (comment/unverified)" in rendered
    assert "Issue #1 references Issue #1" not in rendered
    assert "PR #10 references PR #10" not in rendered


def test_render_relations_ignores_references_inside_code_fences() -> None:
    rendered = render_relations(
        [
            {
                "number": 1,
                "title": "Issue",
                "body": "",
                "comments": [],
            }
        ],
        [
            {
                "number": 2,
                "title": "PR",
                "body": "```python\nFixes #1\n```",
                "comments": [],
            }
        ],
    )

    assert "PR #2 references Issue #1" not in rendered


def test_render_branches_ahead_handles_warning_empty_and_commitless_branch() -> None:
    empty = render_branches_ahead(
        {
            "repo": "example/repo",
            "remote": None,
            "baseBranch": "main",
            "warning": "missing",
            "branches": [],
        }
    )
    commitless = render_branches_ahead(
        {
            "repo": "example/repo",
            "remote": "origin",
            "baseBranch": None,
            "branches": [{"name": "feature", "aheadBy": 1, "behindBy": 0, "commits": []}],
        }
    )

    assert "Warning: missing" in empty
    assert "No branches ahead of main" in empty
    assert "# Branches Ahead Of master" in commitless
    assert "_No commit details listed._" in commitless


def test_render_issue_raises_for_missing_required_title() -> None:
    with pytest.raises(KeyError):
        render_issue({"number": 1})
