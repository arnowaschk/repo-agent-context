from __future__ import annotations

import json
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import repo_agent_context.cli as cli
from repo_agent_context.git import GitDetectionError
from repo_agent_context.model import ContextConfig

FIXTURES = Path(__file__).parent / "fixtures"


def test_version_option_prints_version_and_exits() -> None:
    runner = CliRunner()

    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert f"repo-agent-context {version('repo-agent-context')}" in result.stdout


def test_version_callback_passthrough_when_disabled() -> None:
    assert cli._version_callback(False) is False


def test_load_metadata_reads_json_and_raises_for_missing(tmp_path: Path) -> None:
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text('{"upstream":"owner/repo"}', encoding="utf-8")

    assert cli.load_metadata(metadata_path) == {"upstream": "owner/repo"}

    with pytest.raises(typer.BadParameter):
        cli.load_metadata(tmp_path / "missing.json")


def test_load_metadata_wraps_invalid_json(tmp_path: Path) -> None:
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text("{", encoding="utf-8")

    with pytest.raises(typer.BadParameter, match="not valid JSON"):
        cli.load_metadata(metadata_path)


def test_normalize_provider_accepts_auto_and_known_values() -> None:
    assert cli.normalize_provider("auto") is None
    assert cli.normalize_provider("github") == "github"
    assert cli.normalize_provider("GitLab") == "gitlab"
    assert cli.normalize_provider("SourceForge") == "sourceforge"


def test_normalize_provider_rejects_unknown_value() -> None:
    with pytest.raises(typer.BadParameter, match="Provider must be auto, github, gitlab, or sourceforge."):
        cli.normalize_provider("bitbucket")


def test_write_json_and_text_create_parent_directories(tmp_path: Path) -> None:
    cli.write_json(tmp_path / "nested" / "data.json", {"snowman": "ok", "alpha": "first"})
    cli.write_text(tmp_path / "nested" / "text.md", "hello")

    assert (tmp_path / "nested" / "data.json").read_text(encoding="utf-8") == '{"alpha":"first","snowman":"ok"}'
    assert json.loads((tmp_path / "nested" / "data.json").read_text(encoding="utf-8")) == {
        "alpha": "first",
        "snowman": "ok"
    }
    assert (tmp_path / "nested" / "text.md").read_text(encoding="utf-8") == "hello"


def test_build_context_items_writes_full_items_and_invokes_extra_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_factory: Callable[..., ContextConfig],
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "track", lambda items, description: items)

    result = cli.build_context_items(
        config=config_factory(out_dir=tmp_path / "agent_context", agent_file=tmp_path / "AGENT.md"),
        list_items=lambda: [{"number": 1, "title": "Issue 1"}],
        item_kind="issues",
        item_dir=tmp_path / "agent_context" / "issues",
        list_filename="issues.json",
        view_item=lambda number: {"number": 1, "title": "Issue 1"},
        render_item=lambda item: "rendered",
        extra_writer=lambda number, item: calls.append(number),
    )

    assert result == [{"number": 1, "title": "Issue 1"}]
    assert calls == ["1"]
    assert (tmp_path / "agent_context" / "issues" / "1.json").exists()
    assert (tmp_path / "agent_context" / "issues" / "1.md").read_text(encoding="utf-8") == "rendered"


def test_build_context_items_rejects_missing_number(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_factory: Callable[..., ContextConfig],
) -> None:
    monkeypatch.setattr(cli, "track", lambda items, description: items)

    with pytest.raises(typer.BadParameter, match="missing a number"):
        cli.build_context_items(
            config=config_factory(out_dir=tmp_path / "agent_context", agent_file=tmp_path / "AGENT.md"),
            list_items=lambda: [{}],
            item_kind="issues",
            item_dir=tmp_path / "agent_context" / "issues",
            list_filename="issues.json",
            view_item=lambda number: {},
            render_item=lambda item: "rendered",
        )


def test_build_issues_fetches_list_and_full_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_factory: Callable[..., ContextConfig],
    issue_factory: Callable[..., dict[str, object]],
) -> None:
    monkeypatch.setattr(cli, "track", lambda items, description: items)
    calls: list[tuple[str, object]] = []

    class Provider:
        name = "github"

        def list_issues(self, repo: str, state: str, limit: int) -> list[dict[str, object]]:
            calls.append(("list", (repo, state, limit)))
            return [{"number": 1, "title": "Issue 1"}]

        def view_issue(self, repo: str, number: str) -> dict[str, object]:
            calls.append(("view", (repo, number)))
            return issue_factory(1)

    monkeypatch.setattr(cli, "get_provider", lambda provider: Provider())

    result = cli.build_issues(
        config_factory(out_dir=tmp_path / "agent_context", agent_file=tmp_path / "AGENT.md"),
        Provider(),
    )

    assert result == [issue_factory(1)]
    assert json.loads((tmp_path / "agent_context" / "issues.json").read_text()) == [
        {"number": 1, "title": "Issue 1"}
    ]
    assert (tmp_path / "agent_context" / "issues" / "1.md").exists()
    assert calls == [("list", ("owner/repo", "open", 2)), ("view", ("owner/repo", "1"))]


def test_build_issues_can_include_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_factory: Callable[..., ContextConfig],
) -> None:
    calls: list[tuple[str, object]] = []
    cfg = config_factory(
        out_dir=tmp_path / "agent_context",
        agent_file=tmp_path / "AGENT.md",
        include_closed=True,
    )

    class Provider:
        name = "github"

        def list_issues(self, repo: str, state: str, limit: int) -> list[dict[str, object]]:
            calls.append(("list", (repo, state, limit)))
            return []

        def view_issue(self, repo: str, number: str) -> dict[str, object]:
            raise AssertionError("unexpected view call")

    assert cli.build_issues(cfg, Provider()) == []
    assert calls == [("list", ("owner/repo", "all", 2))]


def test_sourceforge_build_filters_closed_tickets_after_loading_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_factory: Callable[..., ContextConfig],
) -> None:
    monkeypatch.setattr(cli, "track", lambda items, description: items)

    class Provider:
        name = "sourceforge"

        def list_issues(self, repo: str, state: str, limit: int) -> list[dict[str, object]]:
            assert (repo, state) == ("owner/repo", "open")
            return [
                {"number": 446, "title": "Open bug", "state": "UNKNOWN", "tracker": "bugs"},
                {
                    "number": 446,
                    "title": "Feature request",
                    "state": "UNKNOWN",
                    "tracker": "feature-requests",
                },
                {"number": 445, "title": "Closed bug", "state": "UNKNOWN", "tracker": "bugs"},
            ]

        def view_issue(self, repo: str, number: str) -> dict[str, object]:
            tracker, ticket_number = number.rsplit("/", 1)
            state = "CLOSED-FIXED" if ticket_number == "445" else "OPEN"
            return {
                "number": int(ticket_number),
                "title": f"Ticket {ticket_number}",
                "tracker": tracker,
                "state": state,
            }

    out_dir = tmp_path / "agent_context"
    result = cli.build_issues(
        config_factory(
            out_dir=out_dir,
            agent_file=tmp_path / "AGENT.md",
            provider="sourceforge",
        ),
        Provider(),  # type: ignore[arg-type]
    )

    assert [item["tracker"] for item in result] == ["bugs", "feature-requests"]
    assert json.loads((out_dir / "issues.json").read_text(encoding="utf-8")) == result
    assert (out_dir / "issues" / "bugs-446.json").exists()
    assert (out_dir / "issues" / "feature-requests-446.json").exists()
    assert not (out_dir / "issues" / "bugs-445.json").exists()


def test_sourceforge_aggregate_json_uses_hydrated_ticket_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_factory: Callable[..., ContextConfig],
) -> None:
    monkeypatch.setattr(cli, "track", lambda items, description: items)
    list_rows = json.loads((FIXTURES / "sourceforge_issue_list.json").read_text(encoding="utf-8"))
    details = json.loads((FIXTURES / "sourceforge_issue_details.json").read_text(encoding="utf-8"))

    class Provider:
        name = "sourceforge"

        def list_issues(self, repo: str, state: str, limit: int) -> list[dict[str, object]]:
            return list_rows

        def view_issue(self, repo: str, number: str) -> dict[str, object]:
            assert number == "bugs/446"
            return details[0]

    out_dir = tmp_path / "agent_context"
    config = config_factory(
        provider="sourceforge",
        out_dir=out_dir,
        agent_file=tmp_path / "AGENT.md",
        include_closed=True,
    )

    cli.build_issues(config, Provider())  # type: ignore[arg-type]

    assert json.loads((out_dir / "issues.json").read_text(encoding="utf-8")) == details


def test_build_prs_fetches_status_rollup_and_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_factory: Callable[..., ContextConfig],
    pr_factory: Callable[..., dict[str, object]],
) -> None:
    monkeypatch.setattr(cli, "track", lambda items, description: items)
    calls: list[tuple[str, object]] = []

    class Provider:
        name = "github"

        def list_prs(self, repo: str, state: str, limit: int) -> list[dict[str, object]]:
            calls.append(("list", (repo, state, limit)))
            return [{"number": 2, "title": "PR 2", "statusCheckRollup": []}]

        def view_pr(self, repo: str, number: str) -> dict[str, object]:
            calls.append(("view", (repo, number)))
            return pr_factory(2)

        def diff_pr(self, repo: str, number: str) -> str:
            calls.append(("diff", (repo, number)))
            return "diff text"

    result = cli.build_prs(
        config_factory(out_dir=tmp_path / "agent_context", agent_file=tmp_path / "AGENT.md"),
        Provider(),
    )

    assert result == [pr_factory(2)]
    assert calls == [
        ("list", ("owner/repo", "open", 2)),
        ("view", ("owner/repo", "2")),
        ("diff", ("owner/repo", "2")),
    ]
    assert (tmp_path / "agent_context" / "prs" / "2.diff").read_text() == "diff text"


def test_build_branches_writes_json_and_markdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_factory: Callable[..., ContextConfig],
) -> None:
    data = {
        "repo": "owner/repo",
        "remote": "upstream",
        "baseBranch": "main",
        "baseBranchSource": "explicit",
        "branches": [],
    }
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        cli,
        "branches_ahead_of_base",
        lambda repo, base_branch: calls.append((repo, base_branch)) or data,
    )

    assert cli.build_branches(
        config_factory(
            out_dir=tmp_path / "agent_context",
            agent_file=tmp_path / "AGENT.md",
            base_branch="main",
        )
    ) == data
    assert calls == [("owner/repo", "main")]
    assert (tmp_path / "agent_context" / "branches_ahead.json").exists()
    assert (tmp_path / "agent_context" / "index" / "branches_ahead.md").exists()


def test_build_metadata_writes_config(
    tmp_path: Path,
    config_factory: Callable[..., ContextConfig],
) -> None:
    cfg = config_factory(out_dir=tmp_path / "agent_context", agent_file=tmp_path / "AGENT.md")

    cli.build_metadata(cfg)

    metadata = json.loads((tmp_path / "agent_context" / "metadata.json").read_text())
    assert metadata["provider"] == "github"
    assert metadata["upstream"] == "owner/repo"
    assert metadata["fork"] == "fork/repo"
    assert metadata["issue_limit"] == 2
    assert metadata["base_branch"] is None


def test_write_agent_file_skips_existing_unless_overwrite(
    tmp_path: Path,
    config_factory: Callable[..., ContextConfig],
) -> None:
    cfg = config_factory(out_dir=tmp_path / "agent_context", agent_file=tmp_path / "AGENT.md")
    cfg.agent_file.write_text("existing", encoding="utf-8")

    cli.write_agent_file(cfg)
    assert cfg.agent_file.read_text(encoding="utf-8") == "existing"

    overwrite_cfg = ContextConfig(**{**cfg.__dict__, "overwrite_agent": True})
    cli.write_agent_file(overwrite_cfg)
    assert "Upstream repository: `owner/repo`" in cfg.agent_file.read_text(encoding="utf-8")


def test_run_build_orchestrates_all_steps_with_fork(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_factory: Callable[..., ContextConfig],
    issue_factory: Callable[..., dict[str, object]],
    pr_factory: Callable[..., dict[str, object]],
) -> None:
    calls: list[str] = []
    cfg = config_factory(out_dir=tmp_path / "agent_context", agent_file=tmp_path / "AGENT.md")

    class Provider:
        name = "github"

        def check_available(self) -> None:
            calls.append("provider")

        def check_repo_access(self, repo: str) -> None:
            calls.append(f"repo:{repo}")

        def list_issues(self, repo: str, state: str, limit: int) -> list[dict[str, object]]:
            calls.append("issues")
            return [issue_factory(1)]

        def view_issue(self, repo: str, number: str) -> dict[str, object]:
            return issue_factory(1)

        def list_prs(self, repo: str, state: str, limit: int) -> list[dict[str, object]]:
            calls.append("prs")
            return [pr_factory(2)]

        def view_pr(self, repo: str, number: str) -> dict[str, object]:
            return pr_factory(2)

        def diff_pr(self, repo: str, number: str) -> str:
            return "diff text"

    monkeypatch.setattr(cli, "get_provider", lambda provider: Provider())
    monkeypatch.setattr(cli, "build_metadata", lambda config: calls.append("metadata"))
    monkeypatch.setattr(cli, "build_issues", lambda config, provider: [issue_factory(1)])
    monkeypatch.setattr(cli, "build_prs", lambda config, provider: [pr_factory(2)])
    monkeypatch.setattr(
        cli,
        "build_branches",
        lambda config: {"branches": [{"name": "feature"}]},
    )
    monkeypatch.setattr(cli, "write_text", lambda path, text: calls.append(path.name))
    monkeypatch.setattr(cli, "write_agent_file", lambda config: calls.append("agent"))
    monkeypatch.setattr(cli, "update_gitignore", lambda config: calls.append("gitignore"))

    cli.run_build(cfg)

    assert calls[:4] == ["provider", "repo:owner/repo", "repo:fork/repo", "metadata"]
    assert "issues_index.md" in calls
    assert "prs_index.md" in calls
    assert "relations.md" in calls
    assert calls[-2:] == ["agent", "gitignore"]


def test_run_build_skips_fork_check_when_no_fork(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_factory: Callable[..., ContextConfig],
) -> None:
    checked: list[str] = []
    cfg = config_factory(
        out_dir=tmp_path / "agent_context",
        agent_file=tmp_path / "AGENT.md",
        fork=None,
    )

    class Provider:
        name = "github"

        def check_available(self) -> None:
            return None

        def check_repo_access(self, repo: str) -> None:
            checked.append(repo)

        def list_issues(self, repo: str, state: str, limit: int) -> list[dict[str, object]]:
            return []

        def view_issue(self, repo: str, number: str) -> dict[str, object]:
            raise AssertionError("unexpected issue view")

        def list_prs(self, repo: str, state: str, limit: int) -> list[dict[str, object]]:
            return []

        def view_pr(self, repo: str, number: str) -> dict[str, object]:
            raise AssertionError("unexpected pr view")

        def diff_pr(self, repo: str, number: str) -> str:
            raise AssertionError("unexpected diff")

    monkeypatch.setattr(cli, "get_provider", lambda provider: Provider())
    monkeypatch.setattr(cli, "build_metadata", lambda config: None)
    monkeypatch.setattr(cli, "build_issues", lambda config, provider: [])
    monkeypatch.setattr(cli, "build_prs", lambda config, provider: [])
    monkeypatch.setattr(cli, "build_branches", lambda config: {"branches": []})
    monkeypatch.setattr(cli, "write_text", lambda path, text: None)
    monkeypatch.setattr(cli, "write_agent_file", lambda config: None)
    monkeypatch.setattr(cli, "update_gitignore", lambda config: None)

    cli.run_build(cfg)

    assert checked == ["owner/repo"]


def test_status_prints_detected_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    lines: list[str] = []
    monkeypatch.setattr(
        cli,
        "detect_repository_context",
        lambda upstream, fork, provider=None: type(
            "Detected",
            (),
            {"provider": "github", "upstream": "owner/repo", "fork": None},
        )(),
    )
    monkeypatch.setattr(cli.console, "print", lambda text="": lines.append(str(text)))

    cli.status(None, None, None)

    assert any("github" in line for line in lines)
    assert any("owner/repo" in line for line in lines)
    assert any("none" in line for line in lines)


def test_status_wraps_detection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "detect_repository_context",
        lambda upstream, fork, provider=None: (_ for _ in ()).throw(GitDetectionError("bad remote")),
    )

    with pytest.raises(typer.BadParameter):
        cli.status(None, None, None)


def test_build_command_creates_config_and_runs_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[ContextConfig] = []
    monkeypatch.setattr(
        cli,
        "detect_repository_context",
        lambda upstream, fork, provider=None: type(
            "Detected",
            (),
            {"provider": "gitlab", "upstream": "owner/repo", "fork": "fork/repo"},
        )(),
    )
    monkeypatch.setattr(cli.typer, "confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "run_build", lambda cfg: seen.append(cfg))

    cli.build(
        upstream=None,
        fork=None,
        provider="gitlab",
        out=tmp_path / "ctx",
        agent_file=tmp_path / "AGENT.md",
        issue_limit=4,
        pr_limit=5,
        base_branch="main",
        include_closed=True,
        overwrite_agent=True,
        update_gitignore_file=True,
    )

    assert seen[0].out_dir == tmp_path / "ctx"
    assert seen[0].issue_limit == 4
    assert seen[0].base_branch == "main"
    assert seen[0].include_closed is True
    assert seen[0].provider == "gitlab"


def test_build_command_wraps_detection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "detect_repository_context",
        lambda upstream, fork, provider=None: (_ for _ in ()).throw(GitDetectionError("bad remote")),
    )

    with pytest.raises(typer.BadParameter):
        cli.build()


def test_refresh_uses_existing_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "agent_context"
    out.mkdir()
    (out / "metadata.json").write_text(
        json.dumps(
            {
                "upstream": "owner/repo",
                "fork": None,
                "out_dir": str(out),
                "agent_file": str(tmp_path / "CUSTOM.md"),
                "issue_limit": 7,
                "pr_limit": 8,
                "base_branch": "main",
                "include_closed": True,
            }
        ),
        encoding="utf-8",
    )
    seen: list[ContextConfig] = []
    monkeypatch.setattr(cli, "run_build", lambda cfg: seen.append(cfg))

    cli.refresh(out=out, overwrite_agent=True, update_gitignore_file=False)

    assert seen[0].provider == "github"
    assert seen[0].upstream == "owner/repo"
    assert seen[0].agent_file == tmp_path / "CUSTOM.md"
    assert seen[0].issue_limit == 7
    assert seen[0].base_branch == "main"
    assert seen[0].include_closed is True
    assert seen[0].overwrite_agent is True
    assert seen[0].update_gitignore is False


def test_refresh_include_closed_flag_overrides_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "agent_context"
    out.mkdir()
    (out / "metadata.json").write_text(
        json.dumps(
            {
                "upstream": "owner/repo",
                "fork": None,
                "include_closed": False,
            }
        ),
        encoding="utf-8",
    )
    seen: list[ContextConfig] = []
    monkeypatch.setattr(cli, "run_build", lambda cfg: seen.append(cfg))

    cli.refresh(out=out, include_closed=True)

    assert seen[0].include_closed is True


def test_refresh_base_branch_option_overrides_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "agent_context"
    out.mkdir()
    (out / "metadata.json").write_text(
        json.dumps(
            {
                "upstream": "owner/repo",
                "fork": None,
                "base_branch": "main",
            }
        ),
        encoding="utf-8",
    )
    seen: list[ContextConfig] = []
    monkeypatch.setattr(cli, "run_build", lambda cfg: seen.append(cfg))

    cli.refresh(out=out, base_branch="release")

    assert seen[0].base_branch == "release"


def test_refresh_falls_back_to_detection_without_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[ContextConfig] = []
    monkeypatch.setattr(
        cli,
        "detect_repository_context",
        lambda upstream, fork, provider=None: type(
            "Detected",
            (),
            {"provider": "github", "upstream": "owner/repo", "fork": "fork/repo"},
        )(),
    )
    monkeypatch.setattr(cli, "run_build", lambda cfg: seen.append(cfg))

    cli.refresh(out=tmp_path / "missing")

    assert seen[0].upstream == "owner/repo"
    assert seen[0].fork == "fork/repo"
    assert seen[0].out_dir == tmp_path / "missing"
    assert seen[0].agent_file == Path("AGENT.md")
    assert seen[0].base_branch is None
    assert seen[0].include_closed is False
    assert seen[0].provider == "github"


def test_refresh_wraps_detection_errors_without_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "detect_repository_context",
        lambda upstream, fork, provider=None: (_ for _ in ()).throw(GitDetectionError("bad remote")),
    )

    with pytest.raises(typer.BadParameter):
        cli.refresh(out=tmp_path / "missing")


def test_main_callback_is_noop() -> None:
    assert cli.main() is None


def test_print_support_mentions_buymeacoffee_url(monkeypatch: pytest.MonkeyPatch) -> None:
    lines: list[str] = []
    monkeypatch.setattr(cli.console, "print", lambda *args, **kwargs: lines.append(" ".join(str(arg) for arg in args)))

    cli.print_support()

    assert any("Buy Me a Coffee" in line for line in lines)
    assert any(cli.SUPPORT_URL in line for line in lines)
