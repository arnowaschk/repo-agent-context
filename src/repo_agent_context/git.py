from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Any


class GitDetectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitRemotes:
    origin: str | None
    upstream: str | None
    provider: str | None = None


@dataclass(frozen=True)
class RepositoryContext:
    provider: str
    upstream: str
    fork: str | None


def run_git(args: list[str]) -> str:
    command = ["git", *args]
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )

    if result.returncode != 0:
        raise GitDetectionError(
            "Git command failed:\n"
            f"Command: {' '.join(command)}\n"
            f"Exit code: {result.returncode}\n"
            f"Stderr:\n{result.stderr.strip()}"
        )

    return result.stdout


def try_run_git(args: list[str]) -> str | None:
    result = subprocess.run(
        ["git", *args],
        text=True,
        capture_output=True,
        check=False,
    )

    if result.returncode != 0:
        return None

    return result.stdout


def get_remote_url(remote_name: str) -> str | None:
    result = subprocess.run(
        ["git", "remote", "get-url", remote_name],
        text=True,
        capture_output=True,
        check=False,
    )

    if result.returncode != 0:
        return None

    url = result.stdout.strip()
    return url or None


def parse_remote_url(url: str) -> tuple[str, str] | None:
    """
    Convert supported hosting remote URLs to `(provider, repository)`.

    Supported examples:
    - git@github.com:owner/repo.git
    - git@gitlab.com:owner/repo.git
    - https://github.com/owner/repo.git
    - https://gitlab.com/owner/repo.git
    - https://github.com/owner/repo
    - https://gitlab.com/owner/repo
    - ssh://git@github.com/owner/repo.git
    - ssh://git@gitlab.com/owner/repo.git
    - https://git.code.sf.net/p/project/code
    - git://git.code.sf.net/p/project/code
    """
    patterns = [
        ("sourceforge", r"^git@git\.code\.sf\.net:p/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
        ("sourceforge", r"^https://sourceforge\.net/git/p/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
        ("sourceforge", r"^(?:git|https?)://git\.code\.sf\.net/p/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
        ("sourceforge", r"^ssh://[^@]+@git\.code\.sf\.net/p/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
        ("sourceforge", r"^https://sourceforge\.net/p/(?P<owner>[^/]+)/(?:code|git)(?:/.*)?$"),
        ("github", r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
        ("gitlab", r"^git@gitlab\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
        ("github", r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
        ("gitlab", r"^https://gitlab\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
        ("github", r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
        ("gitlab", r"^ssh://git@gitlab\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
    ]

    for provider, pattern in patterns:
        match = re.match(pattern, url)
        if match:
            owner = match.group("owner")
            repo = match.groupdict().get("repo")
            return provider, f"{owner}/{repo}" if repo and provider != "sourceforge" else owner

    return None


def github_repo_from_url(url: str) -> str | None:
    parsed = parse_remote_url(url)
    return parsed[1] if parsed else None


def detect_github_remotes() -> GitRemotes:
    origin_url = get_remote_url("origin")
    upstream_url = get_remote_url("upstream")

    origin_parsed = parse_remote_url(origin_url) if origin_url else None
    upstream_parsed = parse_remote_url(upstream_url) if upstream_url else None
    origin = origin_parsed[1] if origin_parsed else None
    upstream = upstream_parsed[1] if upstream_parsed else None
    provider = upstream_parsed[0] if upstream_parsed else origin_parsed[0] if origin_parsed else None

    return GitRemotes(origin=origin, upstream=upstream, provider=provider)


def remote_name_for_repo(repo: str) -> str | None:
    remote_names = (try_run_git(["remote"]) or "").splitlines()

    for preferred_name in ("upstream", "origin"):
        if preferred_name in remote_names:
            url = get_remote_url(preferred_name)
            if url and (parse_remote_url(url) or (None, None))[1] == repo:
                return preferred_name

    for remote_name in remote_names:
        url = get_remote_url(remote_name)
        if url and (parse_remote_url(url) or (None, None))[1] == repo:
            return remote_name

    return None


def remote_branch_refs(remote_name: str) -> list[tuple[str, str, str]]:
    prefix = f"refs/remotes/{remote_name}/"
    output = try_run_git(
        [
            "for-each-ref",
            "--format=%(refname)%09%(objectname)",
            f"refs/remotes/{remote_name}",
        ]
    )

    if output is None:
        return []

    refs: list[tuple[str, str, str]] = []
    for line in output.splitlines():
        if "\t" not in line:
            continue

        ref_name, oid = line.split("\t", 1)
        ref_name = ref_name.strip()
        oid = oid.strip()

        if not ref_name or not oid:
            continue
        if not ref_name.startswith(prefix):
            continue

        branch_name = ref_name.removeprefix(prefix)
        if branch_name == "HEAD":
            continue

        refs.append((branch_name, ref_name, oid))

    return refs


def parse_rev_list_counts(output: str) -> tuple[int, int]:
    parts = output.split()
    if len(parts) != 2:
        raise GitDetectionError(f"Unexpected branch comparison output: {output!r}")

    behind_text, ahead_text = parts

    try:
        behind_by = int(behind_text)
        ahead_by = int(ahead_text)
    except ValueError as exc:
        raise GitDetectionError(f"Unexpected branch comparison counts: {output!r}") from exc

    return behind_by, ahead_by


def default_branch_for_remote(remote_name: str, fallback: str = "master") -> str:
    output = try_run_git(["symbolic-ref", "--quiet", f"refs/remotes/{remote_name}/HEAD"])
    prefix = f"refs/remotes/{remote_name}/"

    if output:
        ref = output.strip()
        if ref.startswith(prefix):
            branch_name = ref.removeprefix(prefix)
            if branch_name and branch_name != "HEAD":
                return branch_name

    for candidate in ("main", "master"):
        ref_name = f"refs/remotes/{remote_name}/{candidate}"
        if try_run_git(["rev-parse", "--verify", "--quiet", ref_name]):
            return candidate

    return fallback


def commit_summary_from_log_line(line: str) -> dict[str, Any] | None:
    parts = line.split("\x1f", 4)
    if len(parts) != 5:
        return None

    oid, short_oid, authored_at, author_name, subject = parts
    return {
        "oid": oid,
        "shortOid": short_oid,
        "authoredAt": authored_at,
        "authorName": author_name,
        "subject": subject,
    }


def commits_ahead_of_base(ref_name: str, base_ref: str) -> list[dict[str, Any]]:
    output = run_git(
        [
            "log",
            "--format=%H%x1f%h%x1f%aI%x1f%an%x1f%s",
            f"{base_ref}..{ref_name}",
        ]
    )

    commits: list[dict[str, Any]] = []
    for line in output.splitlines():
        commit = commit_summary_from_log_line(line)
        if commit:
            commits.append(commit)

    return commits


def branches_ahead_of_base(repo: str, base_branch: str | None = None) -> dict[str, Any]:
    remote_name = remote_name_for_repo(repo)
    requested_base_branch = base_branch
    if remote_name is None:
        return {
            "repo": repo,
            "remote": None,
            "baseBranch": base_branch or "master",
            "baseBranchSource": "fallback",
            "baseRef": None,
            "branches": [],
            "warning": "No local git remote matches the upstream repository.",
        }

    base_branch = base_branch or default_branch_for_remote(remote_name)
    base_branch_source = "explicit" if requested_base_branch else "detected"
    base_ref = f"refs/remotes/{remote_name}/{base_branch}"
    if try_run_git(["rev-parse", "--verify", "--quiet", base_ref]) is None:
        return {
            "repo": repo,
            "remote": remote_name,
            "baseBranch": base_branch,
            "baseBranchSource": base_branch_source,
            "baseRef": base_ref,
            "branches": [],
            "warning": f"Base branch ref not found locally: {base_ref}",
        }

    branches: list[dict[str, Any]] = []
    warnings: list[str] = []
    for branch_name, ref_name, oid in remote_branch_refs(remote_name):
        if branch_name == base_branch:
            continue

        counts = run_git(["rev-list", "--left-right", "--count", f"{base_ref}...{ref_name}"])
        try:
            behind_by, ahead_by = parse_rev_list_counts(counts)
        except GitDetectionError as exc:
            warnings.append(f"Skipping branch {branch_name}: {exc}")
            continue

        if ahead_by == 0:
            continue

        branches.append(
            {
                "name": branch_name,
                "ref": ref_name,
                "oid": oid,
                "aheadBy": ahead_by,
                "behindBy": behind_by,
                "commits": commits_ahead_of_base(ref_name, base_ref),
            }
        )

    branches.sort(key=lambda branch: (-int(branch["aheadBy"]), str(branch["name"])))

    return {
        "repo": repo,
        "remote": remote_name,
        "baseBranch": base_branch,
        "baseBranchSource": base_branch_source,
        "baseRef": base_ref,
        "branches": branches,
        **({"warning": "; ".join(warnings)} if warnings else {}),
    }


def detect_upstream_and_fork(
    explicit_upstream: str | None,
    explicit_fork: str | None,
) -> tuple[str, str | None]:
    remotes = detect_github_remotes()

    upstream = explicit_upstream or remotes.upstream or remotes.origin
    fork = explicit_fork or remotes.origin

    if upstream is None:
        raise GitDetectionError(
            "Could not determine upstream repository.\n"
            "Pass --upstream owner/repo explicitly or run this command inside a clone with a recognized remote."
        )

    if fork == upstream:
        fork = None

    return upstream, fork


def detect_repository_context(
    explicit_upstream: str | None,
    explicit_fork: str | None,
    explicit_provider: str | None = None,
) -> RepositoryContext:
    remotes = detect_github_remotes()
    parsed_upstream = parse_remote_url(explicit_upstream) if explicit_upstream else None
    sourceforge_path = (
        re.fullmatch(r"/?p/[^/]+/(?:code|git)/?", explicit_upstream.strip()) is not None
        if explicit_upstream
        else False
    )
    provider = explicit_provider or (
        parsed_upstream[0] if parsed_upstream else "sourceforge" if sourceforge_path else remotes.provider
    ) or "github"
    upstream, fork = detect_upstream_and_fork(explicit_upstream, explicit_fork)
    if explicit_fork is None and remotes.provider and remotes.provider != provider:
        fork = None
    if provider == "sourceforge":
        upstream = sourceforge_project_from_identifier(upstream)
        fork = sourceforge_project_from_identifier(fork) if fork else None
    return RepositoryContext(provider=provider, upstream=upstream, fork=fork)


def sourceforge_project_from_identifier(identifier: str) -> str:
    parsed = parse_remote_url(identifier)
    if parsed and parsed[0] == "sourceforge":
        return parsed[1]

    match = re.fullmatch(r"/?p/(?P<project>[^/]+)/(?:code|git)/?", identifier.strip())
    if match:
        return match.group("project")

    return identifier
