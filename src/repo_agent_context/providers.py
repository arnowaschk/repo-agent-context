from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, cast
from urllib.parse import quote

from repo_agent_context.github import (
    check_gh_available,
    gh_json,
    run_gh,
)
from repo_agent_context.github import (
    check_repo_access as check_gh_repo_access,
)
from repo_agent_context.gitlab import (
    GitLabCliError,
    check_glab_available,
    glab_json,
    run_glab,
)
from repo_agent_context.gitlab import (
    check_repo_access as check_glab_repo_access,
)
from repo_agent_context.sourceforge import sourceforge_json


def _normalize_author(raw: dict[str, Any] | None) -> dict[str, Any]:
    author = raw or {}
    login = author.get("login") or author.get("username") or author.get("name") or "unknown"
    return {"login": str(login)}


def _normalize_labels(raw_labels: list[Any] | None) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    for label in raw_labels or []:
        if isinstance(label, str):
            labels.append({"name": label})
            continue
        if isinstance(label, dict):
            name = label.get("name")
            if name:
                labels.append({"name": name})

    return labels


def _normalize_state(raw_state: Any) -> str:
    if raw_state is None:
        return "UNKNOWN"

    state = str(raw_state)
    mapping = {
        "opened": "OPEN",
        "closed": "CLOSED",
        "merged": "MERGED",
        "locked": "LOCKED",
        "success": "SUCCESS",
        "failed": "FAILED",
        "canceled": "CANCELLED",
        "cancelled": "CANCELLED",
        "pending": "PENDING",
        "running": "RUNNING",
        "created": "CREATED",
        "manual": "MANUAL",
        "blocked": "BLOCKED",
        "scheduled": "SCHEDULED",
        "preparing": "PREPARING",
        "waiting_for_resource": "WAITING_FOR_RESOURCE",
        "skipped": "SKIPPED",
    }

    return mapping.get(state.lower(), state.upper())


def _normalize_comments(raw_comments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    for raw in raw_comments or []:
        if raw.get("system"):
            continue

        comments.append(
            {
                "author": _normalize_author(raw.get("author")),
                "createdAt": raw.get("createdAt") or raw.get("created_at") or "",
                "body": raw.get("body") or "",
            }
        )

    return comments


def _diff_stats(diff: str) -> tuple[int, int]:
    additions = 0
    deletions = 0
    for line in diff.splitlines():
        if not line:
            continue
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1

    return additions, deletions


def _normalize_files(raw_changes: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for change in raw_changes or []:
        path = (
            change.get("path")
            or change.get("new_path")
            or change.get("old_path")
            or ""
        )
        diff = change.get("diff") or ""
        additions = change.get("additions")
        deletions = change.get("deletions")
        if additions is None or deletions is None:
            additions, deletions = _diff_stats(diff)

        files.append(
            {
                "path": path,
                "additions": additions,
                "deletions": deletions,
            }
        )

    return files


def _normalize_commits(raw_commits: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    commits: list[dict[str, Any]] = []
    for raw in raw_commits or []:
        oid = raw.get("oid") or raw.get("id") or ""
        commits.append(
            {
                "oid": oid,
                "shortOid": raw.get("shortOid") or raw.get("short_id") or str(oid)[:12],
                "authoredAt": raw.get("authoredAt") or raw.get("authored_date") or raw.get("created_at") or "",
                "authorName": raw.get("authorName") or raw.get("author_name") or raw.get("author") or "",
                "messageHeadline": raw.get("messageHeadline") or raw.get("title") or "",
                "message": raw.get("message") or raw.get("title") or "",
            }
        )

    return commits


def _normalize_issue(raw: dict[str, Any], comments: list[dict[str, Any]]) -> dict[str, Any]:
    number = raw.get("number") or raw.get("iid")
    return {
        "number": int(number) if number is not None else number,
        "title": raw.get("title") or "",
        "state": _normalize_state(raw.get("state")),
        "author": _normalize_author(raw.get("author")),
        "labels": _normalize_labels(raw.get("labels")),
        "createdAt": raw.get("createdAt") or raw.get("created_at") or "",
        "updatedAt": raw.get("updatedAt") or raw.get("updated_at") or "",
        "url": raw.get("url") or raw.get("web_url") or "",
        "body": raw.get("body") or raw.get("description") or "",
        "comments": comments,
    }


def _normalize_ci_checks(
    pipeline: dict[str, Any] | None,
    jobs: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    if pipeline:
        pipeline_id = pipeline.get("id")
        checks.append(
            {
                "name": f"Pipeline {pipeline_id}" if pipeline_id is not None else "Pipeline",
                "status": pipeline.get("status") or "UNKNOWN",
                "detailsUrl": pipeline.get("web_url") or "",
            }
        )

    for job in jobs or []:
        checks.append(
            {
                "name": job.get("name") or job.get("stage") or "unknown",
                "status": job.get("status") or "UNKNOWN",
                "detailsUrl": job.get("web_url") or job.get("url") or "",
            }
        )

    return checks


def _normalize_pr(
    raw: dict[str, Any],
    *,
    comments: list[dict[str, Any]],
    files: list[dict[str, Any]],
    commits: list[dict[str, Any]],
    status_check_rollup: list[dict[str, Any]],
) -> dict[str, Any]:
    number = raw.get("number") or raw.get("iid")
    mergeable = raw.get("mergeable")
    if mergeable is None:
        mergeable = raw.get("detailed_merge_status") or raw.get("merge_status")
        if raw.get("has_conflicts") is True:
            mergeable = "conflicting"

    is_draft = raw.get("isDraft")
    if is_draft is None:
        is_draft = raw.get("draft")
    if is_draft is None:
        is_draft = raw.get("work_in_progress")

    return {
        "number": int(number) if number is not None else number,
        "title": raw.get("title") or "",
        "state": _normalize_state(raw.get("state")),
        "isDraft": bool(is_draft),
        "author": _normalize_author(raw.get("author")),
        "labels": _normalize_labels(raw.get("labels")),
        "createdAt": raw.get("createdAt") or raw.get("created_at") or "",
        "updatedAt": raw.get("updatedAt") or raw.get("updated_at") or "",
        "url": raw.get("url") or raw.get("web_url") or "",
        "body": raw.get("body") or raw.get("description") or "",
        "comments": comments,
        "files": files,
        "commits": commits,
        "baseRefName": raw.get("baseRefName") or raw.get("target_branch") or "",
        "headRefName": raw.get("headRefName") or raw.get("source_branch") or "",
        "mergeable": mergeable,
        "reviewDecision": raw.get("reviewDecision"),
        "statusCheckRollup": status_check_rollup,
    }


def _gitlab_api_json(endpoint: str, *, paginate: bool = False) -> Any:
    args = ["api", endpoint, "--hostname", "gitlab.com"]
    if paginate:
        args.extend(["--paginate", "--output", "json"])
        return _parse_gitlab_json_output(run_glab(args))

    args.extend(["--output", "json"])
    return glab_json(args)


def _parse_gitlab_json_output(output: str) -> Any:
    text = output.strip()
    if not text:
        return []

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        values: list[Any] = []
        idx = 0
        length = len(text)

        while idx < length:
            while idx < length and text[idx].isspace():
                idx += 1

            value, next_idx = decoder.raw_decode(text, idx)
            values.append(value)
            idx = next_idx

        if len(values) == 1:
            return values[0]

        flattened: list[Any] = []
        for value in values:
            if isinstance(value, list):
                flattened.extend(value)
            else:
                flattened.append(value)
        return flattened


def _gitlab_project_path(repo: str) -> str:
    return quote(repo, safe="")


class RepositoryProvider(Protocol):
    @property
    def name(self) -> str: ...

    def check_available(self) -> None: ...

    def check_repo_access(self, repo: str) -> None: ...

    def list_issues(self, repo: str, state: str, limit: int) -> list[dict[str, Any]]: ...

    def view_issue(self, repo: str, number: str) -> dict[str, Any]: ...

    def list_prs(self, repo: str, state: str, limit: int) -> list[dict[str, Any]]: ...

    def view_pr(self, repo: str, number: str) -> dict[str, Any]: ...

    def diff_pr(self, repo: str, number: str) -> str: ...


@dataclass(frozen=True)
class GitHubProvider:
    name: str = "github"

    def check_available(self) -> None:
        check_gh_available()

    def check_repo_access(self, repo: str) -> None:
        check_gh_repo_access(repo)

    def list_issues(self, repo: str, state: str, limit: int) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            gh_json(
                [
                    "issue",
                    "list",
                    "--repo",
                    repo,
                    "--state",
                    "all" if state == "all" else "open",
                    "--limit",
                    str(limit),
                    "--json",
                    "number,title,state,author,labels,createdAt,updatedAt,url,body",
                ]
            ),
        )

    def view_issue(self, repo: str, number: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            gh_json(
                [
                    "issue",
                    "view",
                    number,
                    "--repo",
                    repo,
                    "--comments",
                    "--json",
                    "number,title,state,author,labels,createdAt,updatedAt,url,body,comments",
                ]
            ),
        )

    def list_prs(self, repo: str, state: str, limit: int) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            gh_json(
                [
                    "pr",
                    "list",
                    "--repo",
                    repo,
                    "--state",
                    "all" if state == "all" else "open",
                    "--limit",
                    str(limit),
                    "--json",
                    "number,title,state,author,labels,createdAt,updatedAt,url,body,"
                    "isDraft,mergeable,reviewDecision,headRefName,baseRefName,statusCheckRollup",
                ]
            ),
        )

    def view_pr(self, repo: str, number: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            gh_json(
                [
                    "pr",
                    "view",
                    number,
                    "--repo",
                    repo,
                    "--comments",
                    "--json",
                    "number,title,state,author,labels,createdAt,updatedAt,url,body,"
                    "comments,isDraft,mergeable,reviewDecision,headRefName,baseRefName,"
                    "files,commits,statusCheckRollup",
                ]
            ),
        )

    def diff_pr(self, repo: str, number: str) -> str:
        return run_gh(["pr", "diff", number, "--repo", repo])


@dataclass(frozen=True)
class GitLabProvider:
    name: str = "gitlab"

    def check_available(self) -> None:
        check_glab_available()

    def check_repo_access(self, repo: str) -> None:
        check_glab_repo_access(repo)

    def _state_filters(self, state: str, *, item_kind: str) -> list[str]:
        if state != "all":
            return ["opened" if state == "open" else state]

        if item_kind == "issues":
            return ["opened", "closed"]

        return ["opened", "closed", "merged"]

    def _list_items(self, repo: str, state: str, limit: int, *, item_kind: str) -> list[dict[str, Any]]:
        endpoint_base = f"projects/{_gitlab_project_path(repo)}/{item_kind}"
        page_size = min(max(limit, 1), 100)
        items_by_number: dict[int, dict[str, Any]] = {}

        for filter_state in self._state_filters(state, item_kind=item_kind):
            endpoint = f"{endpoint_base}?state={filter_state}&per_page={page_size}"
            raw_items = cast(list[dict[str, Any]], _gitlab_api_json(endpoint, paginate=True))
            for raw in raw_items or []:
                normalized = _normalize_issue(raw, []) if item_kind == "issues" else _normalize_pr(
                    raw,
                    comments=[],
                    files=[],
                    commits=[],
                    status_check_rollup=[],
                )
                number = normalized["number"]
                if isinstance(number, int):
                    items_by_number[number] = normalized

        items = list(items_by_number.values())
        items.sort(key=lambda item: (str(item.get("updatedAt", "")), int(item.get("number", 0))), reverse=True)
        return items[:limit]

    def list_issues(self, repo: str, state: str, limit: int) -> list[dict[str, Any]]:
        return self._list_items(repo, state, limit, item_kind="issues")

    def view_issue(self, repo: str, number: str) -> dict[str, Any]:
        project_path = _gitlab_project_path(repo)
        issue = cast(dict[str, Any], _gitlab_api_json(f"projects/{project_path}/issues/{number}"))
        comments = _normalize_comments(
            cast(
                list[dict[str, Any]],
                _gitlab_api_json(f"projects/{project_path}/issues/{number}/notes?per_page=100", paginate=True),
            )
        )
        return _normalize_issue(issue, comments)

    def list_prs(self, repo: str, state: str, limit: int) -> list[dict[str, Any]]:
        return self._list_items(repo, state, limit, item_kind="merge_requests")

    def view_pr(self, repo: str, number: str) -> dict[str, Any]:
        project_path = _gitlab_project_path(repo)
        mr = cast(dict[str, Any], _gitlab_api_json(f"projects/{project_path}/merge_requests/{number}"))
        comments = _normalize_comments(
            cast(
                list[dict[str, Any]],
                _gitlab_api_json(f"projects/{project_path}/merge_requests/{number}/notes?per_page=100", paginate=True),
            )
        )
        changes = cast(dict[str, Any], _gitlab_api_json(f"projects/{project_path}/merge_requests/{number}/changes"))
        commits = _normalize_commits(
            cast(
                list[dict[str, Any]],
                _gitlab_api_json(f"projects/{project_path}/merge_requests/{number}/commits", paginate=True),
            )
        )

        pipeline = cast(dict[str, Any] | None, mr.get("head_pipeline"))
        if pipeline is None:
            pipelines = cast(
                list[dict[str, Any]],
                _gitlab_api_json(
                    f"projects/{project_path}/merge_requests/{number}/pipelines?per_page=100",
                    paginate=True,
                ),
            )
            pipeline = pipelines[0] if pipelines else None

        jobs: list[dict[str, Any]] = []
        if pipeline and pipeline.get("id") is not None:
            pipeline_project = pipeline.get("project_id") or mr.get("project_id") or project_path
            try:
                jobs = cast(
                    list[dict[str, Any]],
                    _gitlab_api_json(
                        f"projects/{pipeline_project}/pipelines/{pipeline['id']}/jobs?per_page=100",
                        paginate=True,
                    ),
                )
            except GitLabCliError:
                jobs = []

        status_check_rollup = _normalize_ci_checks(pipeline, jobs)
        files = _normalize_files(changes.get("changes") or [])
        return _normalize_pr(
            mr,
            comments=comments,
            files=files,
            commits=commits,
            status_check_rollup=status_check_rollup,
        )

    def diff_pr(self, repo: str, number: str) -> str:
        return run_glab(["mr", "diff", number, "--repo", repo])


@dataclass
class SourceForgeProvider:
    name: str = "sourceforge"
    _tracker_tools_by_repo: dict[str, list[dict[str, str]]] = field(default_factory=dict, init=False, repr=False)

    def check_available(self) -> None:
        return None

    def check_repo_access(self, repo: str) -> None:
        sourceforge_json(repo)

    def _tracker_tools(self, repo: str) -> list[dict[str, str]]:
        if repo in self._tracker_tools_by_repo:
            return self._tracker_tools_by_repo[repo]
        project = sourceforge_json(repo)
        tools = project.get("tools", []) if isinstance(project, dict) else []
        trackers = [
            {"mount_point": tool["mount_point"], "label": str(tool.get("mount_label", ""))}
            for tool in tools
            if isinstance(tool, dict)
            and tool.get("name") == "tickets"
            and isinstance(tool.get("mount_point"), str)
        ] if isinstance(tools, list) else []
        self._tracker_tools_by_repo[repo] = trackers
        return trackers

    def _issue_trackers(self, repo: str) -> list[str]:
        trackers = [
            tool["mount_point"]
            for tool in self._tracker_tools(repo)
            if tool["mount_point"] != "patches" and tool["label"].lower() != "patches"
        ]
        return trackers or ["tickets"]

    def _patch_tracker(self, repo: str) -> str | None:
        return next(
            (
                tool["mount_point"]
                for tool in self._tracker_tools(repo)
                if tool["mount_point"] == "patches" or tool["label"].lower() == "patches"
            ),
            None,
        )

    @staticmethod
    def _ticket(
        repo: str,
        raw: dict[str, Any],
        comments: list[dict[str, Any]],
        tracker: str = "tickets",
    ) -> dict[str, Any]:
        number = raw.get("ticket_num") or raw.get("number")
        author = raw.get("reported_by") or raw.get("reported_by_id") or "unknown"
        if isinstance(author, str):
            author = {"name": author}
        return {
            "number": int(number) if number is not None else number,
            "title": raw.get("summary") or raw.get("title") or "",
            "tracker": tracker,
            "state": _normalize_state(raw.get("status")),
            "author": _normalize_author(author),
            "labels": _normalize_labels(raw.get("labels")),
            "createdAt": raw.get("created_date") or "",
            "updatedAt": raw.get("mod_date") or "",
            "url": f"https://sourceforge.net/p/{repo}/{tracker}/{number}/" if number is not None else "",
            "body": raw.get("description") or "",
            "comments": comments,
        }

    def list_issues(self, repo: str, state: str, limit: int) -> list[dict[str, Any]]:
        page_size = min(max(limit, 1), 100)
        tickets: list[dict[str, Any]] = []
        for tracker in self._issue_trackers(repo):
            for page in range((limit + page_size - 1) // page_size):
                data = sourceforge_json(f"{repo}/{tracker}?limit={page_size}&page={page}")
                if not isinstance(data, dict):
                    break
                rows = data.get("tickets", [])
                if not isinstance(rows, list) or not rows:
                    break
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    ticket = self._ticket(repo, row, [], tracker)
                    tickets.append(ticket)
        if state != "all":
            tickets = [ticket for ticket in tickets if ticket["state"] in {"OPEN", "UNKNOWN"}]
        return tickets[:limit]

    def view_issue(self, repo: str, number: str) -> dict[str, Any]:
        if "/" in number:
            tracker, ticket_number = number.rsplit("/", 1)
        else:
            tracker, ticket_number = self._issue_trackers(repo)[0], number
        data = sourceforge_json(f"{repo}/{tracker}/{ticket_number}")
        ticket = data.get("ticket", data) if isinstance(data, dict) else {}
        comments: list[dict[str, Any]] = []
        thread = ticket.get("discussion_thread", {}) if isinstance(ticket, dict) else {}
        thread_id = thread.get("_id") if isinstance(thread, dict) else None
        if thread_id:
            discussion = sourceforge_json(f"{repo}/{tracker}/_discuss/thread/{thread_id}")
            posts = discussion.get("posts", []) if isinstance(discussion, dict) else []
            comments = [
                {"author": _normalize_author({"name": post.get("author")}),
                 "createdAt": post.get("timestamp", ""), "body": post.get("text", "")}
                for post in posts if isinstance(post, dict) and not post.get("is_meta")
            ]
        return self._ticket(repo, ticket if isinstance(ticket, dict) else {}, comments, tracker)

    def list_prs(self, repo: str, state: str, limit: int) -> list[dict[str, Any]]:
        tracker = self._patch_tracker(repo)
        if tracker is None:
            return []
        page_size = min(max(limit, 1), 100)
        patches: list[dict[str, Any]] = []
        for page in range((limit + page_size - 1) // page_size):
            data = sourceforge_json(f"{repo}/{tracker}?limit={page_size}&page={page}")
            if not isinstance(data, dict):
                break
            rows = data.get("tickets", [])
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ticket = self._ticket(repo, row, [], tracker)
                pr = _normalize_pr(
                    {
                        "number": ticket["number"],
                        "title": ticket["title"],
                        "state": ticket["state"],
                        "author": ticket["author"],
                        "labels": ticket["labels"],
                        "createdAt": ticket["createdAt"],
                        "updatedAt": ticket["updatedAt"],
                        "url": ticket["url"],
                        "body": ticket["body"],
                    },
                    comments=[],
                    files=[],
                    commits=[],
                    status_check_rollup=[],
                )
                patches.append(pr)
        if state != "all":
            patches = [patch for patch in patches if patch["state"] in {"OPEN", "UNKNOWN"}]
        return patches[:limit]

    def view_pr(self, repo: str, number: str) -> dict[str, Any]:
        tracker = self._patch_tracker(repo)
        if tracker is None:
            raise ValueError("This SourceForge project does not have a patches tracker.")
        data = sourceforge_json(f"{repo}/{tracker}/{number}")
        ticket = data.get("ticket", data) if isinstance(data, dict) else {}
        thread = ticket.get("discussion_thread", {}) if isinstance(ticket, dict) else {}
        posts = thread.get("posts", []) if isinstance(thread, dict) else []
        comments = [
            {
                "author": _normalize_author({"name": post.get("author")}),
                "createdAt": post.get("timestamp", ""),
                "body": post.get("text", ""),
            }
            for post in posts
            if isinstance(post, dict) and not post.get("is_meta")
        ]
        issue = self._ticket(repo, ticket if isinstance(ticket, dict) else {}, comments, tracker)
        return _normalize_pr(
            {
                "number": issue["number"],
                "title": issue["title"],
                "state": issue["state"],
                "author": issue["author"],
                "labels": issue["labels"],
                "createdAt": issue["createdAt"],
                "updatedAt": issue["updatedAt"],
                "url": issue["url"],
                "body": issue["body"],
            },
            comments=comments,
            files=[],
            commits=[],
            status_check_rollup=[],
        )

    def diff_pr(self, repo: str, number: str) -> str:
        return ""


def get_provider(name: str) -> RepositoryProvider:
    normalized = name.strip().lower()
    if normalized == "github":
        return GitHubProvider()
    if normalized == "gitlab":
        return GitLabProvider()
    if normalized == "sourceforge":
        return SourceForgeProvider()

    raise ValueError(f"Unsupported repository provider: {name}")
