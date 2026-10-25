from __future__ import annotations

import subprocess
from typing import Any

import pytest

import repo_agent_context.git as git
from repo_agent_context.git import GitDetectionError


def completed(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["git"], returncode, stdout, stderr)


def test_run_git_returns_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git.subprocess, "run", lambda *args, **kwargs: completed("ok\n"))

    assert git.run_git(["status"]) == "ok\n"


def test_run_git_raises_detailed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        git.subprocess,
        "run",
        lambda *args, **kwargs: completed(stderr="bad", returncode=1),
    )

    with pytest.raises(GitDetectionError) as exc_info:
        git.run_git(["bad"])

    message = str(exc_info.value)
    assert "Command: git bad" in message
    assert "Exit code: 1" in message
    assert "bad" in message


def test_try_run_git_returns_none_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git.subprocess, "run", lambda *args, **kwargs: completed(returncode=128))

    assert git.try_run_git(["status"]) is None


def test_try_run_git_returns_stdout_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git.subprocess, "run", lambda *args, **kwargs: completed("ok"))

    assert git.try_run_git(["status"]) == "ok"


def test_get_remote_url_strips_stdout_and_handles_empty_or_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            completed(" https://github.com/owner/repo.git \n"),
            completed("\n"),
            completed(returncode=2),
        ]
    )
    monkeypatch.setattr(git.subprocess, "run", lambda *args, **kwargs: next(responses))

    assert git.get_remote_url("origin") == "https://github.com/owner/repo.git"
    assert git.get_remote_url("origin") is None
    assert git.get_remote_url("origin") is None


def test_parse_remote_url_returns_none_for_unsupported_url() -> None:
    assert git.parse_remote_url("https://example.com/owner/repo.git") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://git.code.sf.net/p/project/code",
        "git://git.code.sf.net/p/project/code",
        "git@git.code.sf.net:p/project/code",
        "https://sourceforge.net/git/p/project/code",
    ],
)
def test_parse_sourceforge_remote_url(url: str) -> None:
    assert git.parse_remote_url(url) == ("sourceforge", "project")


def test_detect_github_remotes_handles_github_and_gitlab_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {"origin": "https://github.com/owner/repo", "upstream": "https://gitlab.com/x/y"}
    monkeypatch.setattr(git, "get_remote_url", lambda name: values.get(name))

    remotes = git.detect_github_remotes()

    assert remotes.origin == "owner/repo"
    assert remotes.upstream == "x/y"
    assert remotes.provider == "gitlab"


def test_detect_repository_context_uses_detected_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        git,
        "detect_github_remotes",
        lambda: git.GitRemotes(origin="fork/repo", upstream="owner/repo", provider="gitlab"),
    )

    context = git.detect_repository_context(None, None, None)

    assert context.provider == "gitlab"
    assert context.upstream == "owner/repo"
    assert context.fork == "fork/repo"


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("p/gscan2pdf/code", "gscan2pdf"),
        ("https://sourceforge.net/p/gscan2pdf/code", "gscan2pdf"),
        ("https://git.code.sf.net/p/gscan2pdf/code", "gscan2pdf"),
    ],
)
def test_detect_repository_context_normalizes_sourceforge_project(
    monkeypatch: pytest.MonkeyPatch,
    identifier: str,
    expected: str,
) -> None:
    monkeypatch.setattr(
        git,
        "detect_github_remotes",
        lambda: git.GitRemotes(origin=None, upstream=None, provider=None),
    )

    context = git.detect_repository_context(identifier, None, "sourceforge")

    assert context.provider == "sourceforge"
    assert context.upstream == expected


def test_detect_repository_context_infers_sourceforge_from_project_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        git,
        "detect_github_remotes",
        lambda: git.GitRemotes(origin=None, upstream=None, provider=None),
    )

    context = git.detect_repository_context("p/gscan2pdf/code", None, None)

    assert context.provider == "sourceforge"
    assert context.upstream == "gscan2pdf"


def test_detect_repository_context_ignores_origin_from_another_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        git,
        "detect_github_remotes",
        lambda: git.GitRemotes(origin="owner/repo", upstream=None, provider="github"),
    )

    context = git.detect_repository_context("p/gscan2pdf/code", None, "sourceforge")

    assert context.provider == "sourceforge"
    assert context.upstream == "gscan2pdf"
    assert context.fork is None


def test_remote_name_for_repo_prefers_upstream_then_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git, "try_run_git", lambda args: "origin\nupstream\ncustom\n")
    monkeypatch.setattr(
        git,
        "get_remote_url",
        lambda name: {
            "origin": "https://github.com/fork/repo",
            "upstream": "https://github.com/owner/repo",
            "custom": "https://github.com/owner/repo",
        }.get(name),
    )

    assert git.remote_name_for_repo("owner/repo") == "upstream"


def test_remote_name_for_repo_falls_back_to_custom_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git, "try_run_git", lambda args: "mirror\n")
    monkeypatch.setattr(git, "get_remote_url", lambda name: "https://github.com/owner/repo")

    assert git.remote_name_for_repo("owner/repo") == "mirror"


def test_remote_name_for_repo_returns_none_without_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git, "try_run_git", lambda args: None)

    assert git.remote_name_for_repo("owner/repo") is None


def test_remote_branch_refs_filters_head_and_wrong_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        git,
        "try_run_git",
        lambda args: "\n".join(
            [
                "refs/remotes/upstream/master\t111",
                "refs/remotes/upstream/feature/foo\t222",
                "refs/remotes/upstream/HEAD\t333",
                "refs/remotes/origin/feature\t444",
                "refs/remotes/upstream/bad-line",
                "\t555",
            ]
        ),
    )

    assert git.remote_branch_refs("upstream") == [
        ("master", "refs/remotes/upstream/master", "111"),
        ("feature/foo", "refs/remotes/upstream/feature/foo", "222"),
    ]


def test_remote_branch_refs_returns_empty_when_git_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git, "try_run_git", lambda args: None)

    assert git.remote_branch_refs("upstream") == []


def test_default_branch_for_remote_uses_symbolic_head(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        git,
        "try_run_git",
        lambda args: "refs/remotes/upstream/main\n"
        if args[:2] == ["symbolic-ref", "--quiet"]
        else None,
    )

    assert git.default_branch_for_remote("upstream") == "main"


def test_default_branch_for_remote_falls_back_to_existing_main_or_master(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_try_run_git(args: list[str]) -> str | None:
        if args[:2] == ["symbolic-ref", "--quiet"]:
            return None
        if args[-1] == "refs/remotes/upstream/main":
            return "ok"
        return None

    monkeypatch.setattr(git, "try_run_git", fake_try_run_git)

    assert git.default_branch_for_remote("upstream") == "main"


def test_default_branch_for_remote_uses_fallback_when_no_ref_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git, "try_run_git", lambda args: None)

    assert git.default_branch_for_remote("upstream", fallback="trunk") == "trunk"


def test_commit_summary_from_log_line_accepts_separator_in_subject() -> None:
    commit = git.commit_summary_from_log_line("abc\x1fa1\x1f2026\x1fAlice\x1fsubject\x1fextra")

    assert commit == {
        "oid": "abc",
        "shortOid": "a1",
        "authoredAt": "2026",
        "authorName": "Alice",
        "subject": "subject\x1fextra",
    }


def test_commit_summary_from_log_line_rejects_malformed_line() -> None:
    assert git.commit_summary_from_log_line("too\x1ffew") is None


def test_parse_rev_list_counts_rejects_malformed_output() -> None:
    with pytest.raises(GitDetectionError):
        git.parse_rev_list_counts("unexpected output")


def test_parse_rev_list_counts_rejects_wrong_field_count() -> None:
    with pytest.raises(GitDetectionError):
        git.parse_rev_list_counts("bad")


def test_commits_ahead_of_base_skips_malformed_log_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        git,
        "run_git",
        lambda args: "bad\nabc\x1fa1\x1f2026\x1fAlice\x1fAdd thing\n",
    )

    assert git.commits_ahead_of_base("feature", "master") == [
        {
            "oid": "abc",
            "shortOid": "a1",
            "authoredAt": "2026",
            "authorName": "Alice",
            "subject": "Add thing",
        }
    ]


def test_branches_ahead_of_base_reports_no_matching_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git, "remote_name_for_repo", lambda repo: None)

    data = git.branches_ahead_of_base("owner/repo")

    assert data["branches"] == []
    assert data["baseBranch"] == "master"
    assert data["baseBranchSource"] == "fallback"
    assert data["warning"] == "No local git remote matches the upstream repository."


def test_branches_ahead_of_base_reports_missing_base_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git, "remote_name_for_repo", lambda repo: "upstream")
    monkeypatch.setattr(git, "try_run_git", lambda args: None)

    data = git.branches_ahead_of_base("owner/repo", "main")

    assert data["baseRef"] == "refs/remotes/upstream/main"
    assert data["baseBranchSource"] == "explicit"
    assert "Base branch ref not found" in data["warning"]


def test_branches_ahead_of_base_collects_and_sorts_ahead_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git, "remote_name_for_repo", lambda repo: "upstream")
    monkeypatch.setattr(git, "try_run_git", lambda args: "base-ok")
    monkeypatch.setattr(git, "default_branch_for_remote", lambda remote: "main")
    monkeypatch.setattr(
        git,
        "remote_branch_refs",
        lambda remote: [
            ("main", "refs/remotes/upstream/main", "000"),
            ("small", "refs/remotes/upstream/small", "111"),
            ("stale", "refs/remotes/upstream/stale", "222"),
            ("large", "refs/remotes/upstream/large", "333"),
        ],
    )

    def fake_run_git(args: list[str]) -> str:
        value = args[-1]
        if value.endswith("...refs/remotes/upstream/small"):
            return "0 1"
        if value.endswith("...refs/remotes/upstream/stale"):
            return "5 0"
        if value.endswith("...refs/remotes/upstream/large"):
            return "2 3"
        raise AssertionError(args)

    def fake_commits(ref_name: str, base_ref: str) -> list[dict[str, Any]]:
        return [{"shortOid": ref_name.rsplit("/", 1)[-1], "subject": "commit"}]

    monkeypatch.setattr(git, "run_git", fake_run_git)
    monkeypatch.setattr(git, "commits_ahead_of_base", fake_commits)

    data = git.branches_ahead_of_base("owner/repo")

    assert data["baseBranch"] == "main"
    assert data["baseBranchSource"] == "detected"
    assert [branch["name"] for branch in data["branches"]] == ["large", "small"]
    assert data["branches"][0]["aheadBy"] == 3
    assert data["branches"][0]["behindBy"] == 2


def test_branches_ahead_of_base_skips_malformed_counts_and_reports_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git, "remote_name_for_repo", lambda repo: "upstream")
    monkeypatch.setattr(git, "default_branch_for_remote", lambda remote: "main")
    monkeypatch.setattr(git, "remote_branch_refs", lambda remote: [("feature", "refs/remotes/upstream/feature", "111")])

    def fake_try_run_git(args: list[str]) -> str | None:
        if args[:3] == ["rev-parse", "--verify", "--quiet"]:
            return "ok"
        return None

    monkeypatch.setattr(git, "try_run_git", fake_try_run_git)
    monkeypatch.setattr(git, "run_git", lambda args: "unexpected output")

    data = git.branches_ahead_of_base("owner/repo")

    assert data["branches"] == []
    assert "Skipping branch feature" in data["warning"]


def test_detect_upstream_and_fork_uses_explicit_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git, "detect_github_remotes", lambda: git.GitRemotes(None, None))

    assert git.detect_upstream_and_fork("owner/repo", "fork/repo") == ("owner/repo", "fork/repo")


def test_detect_upstream_and_fork_prefers_upstream_and_drops_same_fork(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        git,
        "detect_github_remotes",
        lambda: git.GitRemotes(origin="owner/repo", upstream="owner/repo"),
    )

    assert git.detect_upstream_and_fork(None, None) == ("owner/repo", None)


def test_detect_upstream_and_fork_falls_back_to_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        git,
        "detect_github_remotes",
        lambda: git.GitRemotes(origin="owner/repo", upstream=None),
    )

    assert git.detect_upstream_and_fork(None, None) == ("owner/repo", None)


def test_detect_upstream_and_fork_raises_without_any_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git, "detect_github_remotes", lambda: git.GitRemotes(None, None))

    with pytest.raises(GitDetectionError):
        git.detect_upstream_and_fork(None, None)
