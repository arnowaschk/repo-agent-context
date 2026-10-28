from __future__ import annotations

import json
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.progress import track

from repo_agent_context.git import (
    GitDetectionError,
    branches_ahead_of_base,
    detect_repository_context,
)
from repo_agent_context.model import ContextConfig
from repo_agent_context.providers import RepositoryProvider, get_provider
from repo_agent_context.render import (
    render_branches_ahead,
    render_issue,
    render_issues_index,
    render_pr,
    render_prs_index,
    render_relations,
)
from repo_agent_context.templates import render_agent_md

app = typer.Typer(no_args_is_help=True)
console = Console()
DEFAULT_AGENT_FILE = Path("AGENT.md")
DEFAULT_OUT_DIR = Path("agent_context")
SUPPORT_URL = "https://buymeacoffee.com/arnwas"


def print_support() -> None:
    console.print()
    console.print("If repo-agent-context saves you maintainer time, support is appreciated:")
    console.print(f"Buy Me a Coffee: [bold]{SUPPORT_URL}[/bold]")


def load_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise typer.BadParameter(
            f"Metadata file not found: {path}\nRun `repo-agent-context build` first."
        )

    try:
        metadata: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"Metadata file is not valid JSON: {path}") from exc

    return metadata


def normalize_provider(provider: str | None) -> str | None:
    if provider is None:
        return None

    normalized = provider.strip().lower()
    if normalized == "auto":
        return None
    if normalized not in {"github", "gitlab", "sourceforge"}:
        raise typer.BadParameter("Provider must be auto, github, gitlab, or sourceforge.")

    return normalized


def update_gitignore(config: ContextConfig) -> None:
    if not config.update_gitignore:
        return

    gitignore_path = Path(".gitignore")

    wanted_entries = [
        f"{config.out_dir.as_posix().rstrip('/')}/",
        config.agent_file.as_posix(),
    ]

    if gitignore_path.exists():
        existing_text = gitignore_path.read_text(encoding="utf-8")
    else:
        existing_text = ""

    existing_lines = {
        line.strip()
        for line in existing_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    missing_entries = [entry for entry in wanted_entries if entry not in existing_lines]

    if not missing_entries:
        return

    parts: list[str] = []

    if existing_text:
        parts.append(existing_text.rstrip())
        parts.append("")

    parts.append("# Local coding-agent context")
    parts.extend(missing_entries)
    parts.append("")

    gitignore_path.write_text("\n".join(parts), encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_context_items(
    *,
    config: ContextConfig,
    list_items: Callable[[], list[dict[str, Any]]],
    item_kind: str,
    item_dir: Path,
    list_filename: str,
    view_item: Callable[[str], dict[str, Any]],
    render_item: Callable[[dict[str, Any]], str],
    extra_writer: Callable[[str, dict[str, Any]], None] | None = None,
    include_item: Callable[[dict[str, Any]], bool] | None = None,
    view_identifier: Callable[[dict[str, Any]], str] | None = None,
    item_filename: Callable[[dict[str, Any]], str] | None = None,
    aggregate_full_items: bool = False,
) -> list[dict[str, Any]]:
    items = list_items()

    full_items: list[dict[str, Any]] = []
    included_items: list[dict[str, Any]] = []
    for item in track(items, description=f"Fetching {item_kind}"):
        number_value = item.get("number")
        if number_value is None:
            raise typer.BadParameter(f"{item_kind.capitalize()} entry is missing a number: {item!r}")

        number = str(number_value)
        full = view_item(view_identifier(item) if view_identifier is not None else number)
        if include_item is not None and not include_item(full):
            continue

        included_items.append(item)
        full_items.append(full)
        filename = item_filename(item) if item_filename is not None else number
        write_json(item_dir / f"{filename}.json", full)
        write_text(item_dir / f"{filename}.md", render_item(full))

        if extra_writer is not None:
            extra_writer(number, full)

    aggregate_items = full_items if aggregate_full_items else included_items
    write_json(config.out_dir / list_filename, aggregate_items)
    return full_items


def _is_open_sourceforge_item(item: dict[str, Any]) -> bool:
    return item.get("state") == "OPEN"


def _sourceforge_issue_identifier(item: dict[str, Any]) -> str:
    return f"{item.get('tracker', 'tickets')}/{item['number']}"


def _sourceforge_issue_filename(item: dict[str, Any]) -> str:
    return f"{item.get('tracker', 'tickets')}-{item['number']}"


def build_issues(config: ContextConfig, provider: RepositoryProvider) -> list[dict[str, Any]]:
    state = "all" if config.include_closed else "open"
    include_item = None
    view_identifier = None
    item_filename = None
    if provider.name == "sourceforge" and not config.include_closed:
        include_item = _is_open_sourceforge_item
    if provider.name == "sourceforge":
        view_identifier = _sourceforge_issue_identifier
        item_filename = _sourceforge_issue_filename

    return build_context_items(
        config=config,
        list_items=lambda: provider.list_issues(config.upstream, state, config.issue_limit),
        item_kind="issues",
        item_dir=config.out_dir / "issues",
        list_filename="issues.json",
        view_item=lambda number: provider.view_issue(config.upstream, number),
        render_item=render_issue,
        include_item=include_item,
        view_identifier=view_identifier,
        item_filename=item_filename,
        aggregate_full_items=provider.name == "sourceforge",
    )


def build_prs(config: ContextConfig, provider: RepositoryProvider) -> list[dict[str, Any]]:
    state = "all" if config.include_closed else "open"
    item_dir = config.out_dir / "prs"

    def write_diff(number: str, _: dict[str, Any]) -> None:
        diff = provider.diff_pr(config.upstream, number)
        write_text(item_dir / f"{number}.diff", diff)

    include_item = None
    if provider.name == "sourceforge" and not config.include_closed:
        include_item = _is_open_sourceforge_item

    return build_context_items(
        config=config,
        list_items=lambda: provider.list_prs(config.upstream, state, config.pr_limit),
        item_kind="pull requests",
        item_dir=item_dir,
        list_filename="prs.json",
        view_item=lambda number: provider.view_pr(config.upstream, number),
        render_item=render_pr,
        extra_writer=write_diff,
        include_item=include_item,
    )


def build_branches(config: ContextConfig) -> dict[str, Any]:
    branches = branches_ahead_of_base(config.upstream, config.base_branch)
    write_json(config.out_dir / "branches_ahead.json", branches)
    write_text(config.out_dir / "index" / "branches_ahead.md", render_branches_ahead(branches))
    return branches


def build_metadata(config: ContextConfig) -> None:
    metadata = {
        "provider": config.provider,
        "upstream": config.upstream,
        "fork": config.fork,
        "out_dir": str(config.out_dir),
        "agent_file": str(config.agent_file),
        "issue_limit": config.issue_limit,
        "pr_limit": config.pr_limit,
        "base_branch": config.base_branch,
        "include_closed": config.include_closed,
    }

    write_json(config.out_dir / "metadata.json", metadata)


def write_agent_file(config: ContextConfig) -> None:
    if config.agent_file.exists() and not config.overwrite_agent:
        console.print(
            f"[yellow]Skipping existing {config.agent_file}. "
            "Use --overwrite-agent to replace it.[/yellow]"
        )
        return

    text = render_agent_md(
        upstream=config.upstream,
        fork=config.fork,
        context_dir=str(config.out_dir),
    )
    write_text(config.agent_file, text)


def run_build(config: ContextConfig) -> None:
    provider = get_provider(config.provider)

    console.print("[bold]Detected repository configuration:[/bold]")
    console.print(f"Provider: [bold]{config.provider}[/bold]")
    console.print(f"Upstream: [bold]{config.upstream}[/bold]")
    console.print(f"Fork: [bold]{config.fork or 'none'}[/bold]")
    console.print(f"Output directory: [bold]{config.out_dir}[/bold]")
    console.print(f"Agent file: [bold]{config.agent_file}[/bold]")
    console.print(f"Branch-ahead base: [bold]{config.base_branch or 'auto'}[/bold]")
    console.print()

    console.print(f"[bold]Checking {config.provider} availability...[/bold]")
    provider.check_available()

    console.print(f"[bold]Checking upstream repository:[/bold] {config.upstream}")
    provider.check_repo_access(config.upstream)

    if config.fork:
        console.print(f"[bold]Checking fork repository:[/bold] {config.fork}")
        provider.check_repo_access(config.fork)

    config.out_dir.mkdir(parents=True, exist_ok=True)

    build_metadata(config)

    issues = build_issues(config, provider)
    prs = build_prs(config, provider)
    branches = build_branches(config)

    write_text(config.out_dir / "index" / "issues_index.md", render_issues_index(issues))
    write_text(config.out_dir / "index" / "prs_index.md", render_prs_index(prs))
    write_text(config.out_dir / "index" / "relations.md", render_relations(issues, prs))

    write_agent_file(config)

    update_gitignore(config)

    console.print()
    console.print("[green]Done.[/green]")
    console.print(f"Context directory: [bold]{config.out_dir}[/bold]")
    console.print(f"Agent file: [bold]{config.agent_file}[/bold]")
    console.print(f"Issues fetched: [bold]{len(issues)}[/bold]")
    console.print(f"Pull requests fetched: [bold]{len(prs)}[/bold]")
    console.print(
        f"Branches ahead of {branches.get('baseBranch')}: "
        f"[bold]{len(branches.get('branches') or [])}[/bold]"
    )
    print_support()


@app.command()
def status(
    upstream: Annotated[str | None, typer.Option("--upstream", "-u")] = None,
    fork: Annotated[str | None, typer.Option("--fork", "-f")] = None,
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            help="Repository provider: auto, github, gitlab, or sourceforge.",
        ),
    ] = None,
) -> None:
    try:
        detected = detect_repository_context(upstream, fork, normalize_provider(provider))
    except GitDetectionError as exc:
        raise typer.BadParameter(str(exc)) from exc

    console.print(f"Provider: [bold]{detected.provider}[/bold]")
    console.print(f"Upstream: [bold]{detected.upstream}[/bold]")
    console.print(f"Fork: [bold]{detected.fork or 'none'}[/bold]")
    print_support()


@app.command()
def build(
    upstream: Annotated[
        str | None,
        typer.Option(
            "--upstream",
            "-u",
            help="Upstream repository, e.g. arnowaschk/repo-agent-context. "
            "If omitted, it is detected from the git remote named 'upstream', "
            "falling back to 'origin'.",
        ),
    ] = None,
    fork: Annotated[
        str | None,
        typer.Option(
            "--fork",
            "-f",
            help="Fork repository, e.g. myname/repo-agent-context. "
            "If omitted, it is detected from the git remote named 'origin'.",
        ),
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            help="Repository provider: auto, github, gitlab, or sourceforge.",
        ),
    ] = None,
    out: Annotated[
        Path,
        typer.Option(
            "--out",
            "-o",
            help="Output directory for generated context.",
        ),
    ] = DEFAULT_OUT_DIR,
    agent_file: Annotated[
        Path,
        typer.Option(
            "--agent-file",
            help="Path of generated AGENT.md.",
        ),
    ] = DEFAULT_AGENT_FILE,
    issue_limit: Annotated[
        int, typer.Option("--issue-limit", help="Maximum number of issues to fetch.")
    ] = 300,
    pr_limit: Annotated[
        int, typer.Option("--pr-limit", help="Maximum number of pull requests to fetch.")
    ] = 300,
    include_closed: Annotated[
        bool,
        typer.Option("--include-closed", help="Fetch closed issues and PRs as well."),
    ] = False,
    overwrite_agent: Annotated[
        bool,
        typer.Option("--overwrite-agent", help="Overwrite AGENT.md if it already exists."),
    ] = False,
    update_gitignore_file: Annotated[
        bool,
        typer.Option(
            "--update-gitignore/--no-update-gitignore",
            help="Create or update .gitignore with generated context paths.",
        ),
    ] = True,
    base_branch: Annotated[
        str | None,
        typer.Option(
            "--base-branch",
            help="Base branch for branch-ahead context. If omitted, detect the upstream default.",
        ),
    ] = None,
) -> None:
    try:
        detected = detect_repository_context(upstream, fork, normalize_provider(provider))
    except GitDetectionError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if upstream is None or fork is None or normalize_provider(provider) is None:
        console.print("Resolved repository settings:")
        console.print(f"  Provider: {detected.provider}")
        console.print(f"  Upstream: {detected.upstream}")
        console.print(f"  Fork: {detected.fork or 'none'}")
        if not typer.confirm("Use these settings to build the context?", default=True):
            raise typer.Abort()

    config = ContextConfig(
        provider=detected.provider,
        upstream=detected.upstream,
        fork=detected.fork,
        out_dir=out,
        agent_file=agent_file,
        issue_limit=issue_limit,
        pr_limit=pr_limit,
        base_branch=base_branch,
        include_closed=include_closed,
        overwrite_agent=overwrite_agent,
        update_gitignore=update_gitignore_file,
    )

    run_build(config)


@app.command()
def refresh(
    out: Annotated[
        Path,
        typer.Option(
            "--out",
            "-o",
            help="Context directory containing metadata.json. Default: agent_context.",
        ),
    ] = DEFAULT_OUT_DIR,
    upstream: Annotated[
        str | None,
        typer.Option(
            "--upstream",
            "-u",
            help=(
                "Upstream repository. Used only if metadata.json is missing "
                "or to override it."
            ),
        ),
    ] = None,
    fork: Annotated[
        str | None,
        typer.Option(
            "--fork",
            "-f",
            help="Fork repository. Used only if metadata.json is missing or to override it.",
        ),
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            help="Repository provider: auto, github, gitlab, or sourceforge.",
        ),
    ] = None,
    issue_limit: Annotated[
        int, typer.Option("--issue-limit", help="Maximum number of issues to fetch.")
    ] = 300,
    pr_limit: Annotated[
        int, typer.Option("--pr-limit", help="Maximum number of pull requests to fetch.")
    ] = 300,
    include_closed: Annotated[
        bool,
        typer.Option("--include-closed", help="Fetch closed issues and PRs as well."),
    ] = False,
    overwrite_agent: Annotated[
        bool,
        typer.Option("--overwrite-agent", help="Overwrite AGENT.md during refresh."),
    ] = False,
    update_gitignore_file: Annotated[
        bool,
        typer.Option(
            "--update-gitignore/--no-update-gitignore",
            help="Create or update .gitignore with generated context paths.",
        ),
    ] = True,
    base_branch: Annotated[
        str | None,
        typer.Option(
            "--base-branch",
            help="Override the branch-ahead base branch from metadata or auto-detection.",
        ),
    ] = None,
) -> None:
    metadata_path = out / "metadata.json"

    if metadata_path.exists():
        metadata = load_metadata(metadata_path)
        metadata_provider = normalize_provider(metadata.get("provider")) or "github"

        config = ContextConfig(
            provider=normalize_provider(provider) or metadata_provider,
            upstream=metadata["upstream"],
            fork=metadata.get("fork"),
            out_dir=Path(metadata.get("out_dir", str(out))),
            agent_file=Path(metadata.get("agent_file", "AGENT.md")),
            issue_limit=int(metadata.get("issue_limit", issue_limit)),
            pr_limit=int(metadata.get("pr_limit", pr_limit)),
            base_branch=base_branch or metadata.get("base_branch"),
            include_closed=include_closed or bool(metadata.get("include_closed", False)),
            overwrite_agent=overwrite_agent,
            update_gitignore=update_gitignore_file,
        )

    else:
        console.print(
            f"[yellow]No metadata file found at {metadata_path}. "
            "Falling back to repository auto-detection.[/yellow]"
        )

        try:
            detected = detect_repository_context(upstream, fork, normalize_provider(provider))
        except GitDetectionError as exc:
            raise typer.BadParameter(str(exc)) from exc

        config = ContextConfig(
            provider=detected.provider,
            upstream=detected.upstream,
            fork=detected.fork,
            out_dir=out,
            agent_file=Path("AGENT.md"),
            issue_limit=300,
            pr_limit=300,
            base_branch=base_branch,
            include_closed=include_closed,
            overwrite_agent=overwrite_agent,
            update_gitignore=update_gitignore_file,
        )

    run_build(config)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the installed version and exit.",
            is_eager=True,
            callback=lambda value: _version_callback(value),
        ),
    ] = False,
) -> None:
    return None


def _version_callback(value: bool) -> bool:
    if value:
        console.print(f"repo-agent-context {version('repo-agent-context')}")
        raise typer.Exit()

    return value
