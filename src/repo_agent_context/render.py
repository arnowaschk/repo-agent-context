from __future__ import annotations

import re
from typing import Any

ISSUE_REFERENCE_RE = re.compile(
    r"(?i)\b("
    r"fix(?:e[sd])?|"
    r"close[sd]?|"
    r"resolve[sd]?|"
    r"ref(?:s|erence[sd]?)?|"
    r"relate[sd]?"
    r")?\s*#(?P<number>\d+)"
)
FENCED_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)

CI_STATE_PRIORITY = {
    "FAILURE": 0,
    "ERROR": 0,
    "FAILED": 0,
    "ACTION_REQUIRED": 1,
    "BLOCKED": 1,
    "MANUAL": 1,
    "PENDING": 2,
    "CREATED": 2,
    "QUEUED": 2,
    "RUNNING": 2,
    "REQUESTED": 2,
    "STARTED": 2,
    "PREPARING": 2,
    "WAITING_FOR_RESOURCE": 2,
    "SCHEDULED": 2,
    "CANCELLED": 3,
    "TIMED_OUT": 3,
    "SUCCESS": 4,
    "NEUTRAL": 5,
    "SKIPPED": 5,
    "UNKNOWN": 6,
}


def extract_references_from_text(text: str) -> set[int]:
    references: set[int] = set()
    text_without_fenced_code = FENCED_CODE_BLOCK_RE.sub("", text or "")

    for match in ISSUE_REFERENCE_RE.finditer(text_without_fenced_code):
        references.add(int(match.group("number")))

    return references


def extract_references_from_comments(comments: list[dict[str, Any]]) -> set[int]:
    references: set[int] = set()

    for comment in comments:
        references.update(extract_references_from_text(comment.get("body") or ""))

    return references


def issue_identity(issue: dict[str, Any]) -> str:
    number = issue["number"]
    tracker = issue.get("tracker")
    return f"{tracker}/{number}" if tracker else f"#{number}"


def render_relations(
    issues: list[dict[str, Any]],
    prs: list[dict[str, Any]],
) -> str:
    issues_by_number: dict[int, list[dict[str, Any]]] = {}
    for issue in issues:
        issues_by_number.setdefault(int(issue["number"]), []).append(issue)
    issue_numbers = set(issues_by_number)
    pr_numbers = {int(pr["number"]) for pr in prs}

    lines: list[str] = [
        "# Relations",
        "",
        "This file lists detected references between issues and pull requests.",
        "",
        "Detection is based on textual references such as `#123`, `Fixes #123`, "
        "`Closes #123`, `Resolves #123`, `Refs #123`, and similar forms.",
        "",
    ]

    pr_issue_relations: list[tuple[str, str, str]] = []
    pr_pr_relations: list[tuple[str, str, str]] = []
    issue_issue_relations: list[tuple[str, str, str]] = []
    issue_pr_relations: list[tuple[str, str, str]] = []

    def issue_label(issue: dict[str, Any]) -> str:
        return f"Issue {issue_identity(issue)}"

    def issue_target_label(number: int) -> tuple[str, bool]:
        matches = issues_by_number.get(number, [])
        if len(matches) > 1:
            identities = ", ".join(sorted(issue_identity(issue) for issue in matches))
            return f"Issue #{number} (ambiguous: {identities})", True
        if matches:
            return issue_label(matches[0]), False
        return f"Issue #{number}", False

    for pr in prs:
        pr_number = int(pr["number"])
        pr_label = f"PR #{pr_number}"

        body_refs = extract_references_from_text(pr.get("body") or "")
        comment_refs = extract_references_from_comments(pr.get("comments") or [])

        for ref in sorted(body_refs):
            if ref == pr_number:
                continue
            if ref in issue_numbers:
                target, ambiguous = issue_target_label(ref)
                pr_issue_relations.append((pr_label, target, "body/ambiguous" if ambiguous else "body"))
            elif ref in pr_numbers:
                pr_pr_relations.append((pr_label, f"PR #{ref}", "body"))
            else:
                pr_issue_relations.append((pr_label, f"Issue #{ref}", "body/unverified"))

        for ref in sorted(comment_refs):
            if ref == pr_number:
                continue
            if ref in issue_numbers:
                target, ambiguous = issue_target_label(ref)
                pr_issue_relations.append((pr_label, target, "comment/ambiguous" if ambiguous else "comment"))
            elif ref in pr_numbers:
                pr_pr_relations.append((pr_label, f"PR #{ref}", "comment"))
            else:
                pr_issue_relations.append((pr_label, f"Issue #{ref}", "comment/unverified"))

    for issue in issues:
        issue_number = int(issue["number"])
        source_label = issue_label(issue)

        body_refs = extract_references_from_text(issue.get("body") or "")
        comment_refs = extract_references_from_comments(issue.get("comments") or [])

        for ref in sorted(body_refs):
            matches = issues_by_number.get(ref, [])
            if ref == issue_number and len(matches) == 1:
                continue
            if ref in pr_numbers:
                issue_pr_relations.append((source_label, f"PR #{ref}", "body"))
            elif matches:
                target, ambiguous = issue_target_label(ref)
                issue_issue_relations.append((source_label, target, "body/ambiguous" if ambiguous else "body"))
            else:
                issue_issue_relations.append((source_label, f"Issue #{ref}", "body/unverified"))

        for ref in sorted(comment_refs):
            matches = issues_by_number.get(ref, [])
            if ref == issue_number and len(matches) == 1:
                continue
            if ref in pr_numbers:
                issue_pr_relations.append((source_label, f"PR #{ref}", "comment"))
            elif matches:
                target, ambiguous = issue_target_label(ref)
                issue_issue_relations.append((source_label, target, "comment/ambiguous" if ambiguous else "comment"))
            else:
                issue_issue_relations.append((source_label, f"Issue #{ref}", "comment/unverified"))

    def unique_relations(
        relations: list[tuple[str, str, str]],
    ) -> list[tuple[str, str, str]]:
        seen: set[tuple[str, str, str]] = set()
        result: list[tuple[str, str, str]] = []

        for relation in relations:
            if relation not in seen:
                seen.add(relation)
                result.append(relation)

        return sorted(result)

    lines.extend(["## Pull requests referencing issues", ""])

    for pr_label, issue_label_text, source in unique_relations(pr_issue_relations):
        lines.append(f"- {pr_label} references {issue_label_text} ({source})")

    if not pr_issue_relations:
        lines.append("_No PR-to-issue references detected._")

    lines.extend(["", "## Issues referencing pull requests", ""])

    for issue_label_text, pr_label, source in unique_relations(issue_pr_relations):
        lines.append(f"- {issue_label_text} references {pr_label} ({source})")

    if not issue_pr_relations:
        lines.append("_No issue-to-PR references detected._")

    lines.extend(["", "## Issues referencing issues", ""])

    for source_issue, target_issue, source in unique_relations(issue_issue_relations):
        lines.append(f"- {source_issue} references {target_issue} ({source})")

    if not issue_issue_relations:
        lines.append("_No issue-to-issue references detected._")

    lines.extend(["", "## Pull requests referencing pull requests", ""])

    for source_pr, target_pr, source in unique_relations(pr_pr_relations):
        lines.append(f"- {source_pr} references {target_pr} ({source})")

    if not pr_pr_relations:
        lines.append("_No PR-to-PR references detected._")

    lines.append("")
    return "\n".join(lines)


def author_login(item: dict[str, Any]) -> str:
    author = item.get("author") or {}
    return author.get("login") or author.get("username") or author.get("name") or "unknown"


def label_names(item: dict[str, Any]) -> str:
    labels = item.get("labels") or []
    names = [label.get("name", "") if isinstance(label, dict) else str(label) for label in labels]
    return ", ".join(name for name in names if name)


def check_name(check: dict[str, Any]) -> str:
    return (
        check.get("name")
        or check.get("context")
        or check.get("workflowName")
        or check.get("title")
        or "unknown"
    )


def check_state(check: dict[str, Any]) -> str:
    conclusion = check.get("conclusion")
    status = check.get("status")
    state = check.get("state")
    return str(conclusion or status or state or "UNKNOWN").upper()


def ci_status_counts(status_check_rollup: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in status_check_rollup:
        state = check_state(check)
        counts[state] = counts.get(state, 0) + 1

    return counts


def ci_summary(status_check_rollup: list[dict[str, Any]]) -> str:
    if not status_check_rollup:
        return "no checks"

    counts = ci_status_counts(status_check_rollup)

    def sort_key(item: tuple[str, int]) -> tuple[int, str]:
        state, _ = item
        return (CI_STATE_PRIORITY.get(state, 99), state)

    return ", ".join(f"{state.lower()}: {count}" for state, count in sorted(counts.items(), key=sort_key))


def is_attention_check(check: dict[str, Any]) -> bool:
    return check_state(check) not in {"SUCCESS", "SKIPPED", "NEUTRAL"}


def render_ci_status(status_check_rollup: list[dict[str, Any]]) -> str:
    if not status_check_rollup:
        return "_No CI status checks listed._"

    attention_checks = sorted(
        (check for check in status_check_rollup if is_attention_check(check)),
        key=lambda check: (
            CI_STATE_PRIORITY.get(check_state(check), 99),
            check_name(check).lower(),
        ),
    )
    lines = [f"Summary: {ci_summary(status_check_rollup)}"]

    if not attention_checks:
        lines.append("No failing or pending checks.")
        return "\n".join(lines)

    lines.append("")
    lines.append("Checks requiring attention:")
    for check in attention_checks:
        state = check_state(check).lower()
        details_url = check.get("detailsUrl") or check.get("targetUrl") or check.get("url")
        suffix = f" ({details_url})" if details_url else ""
        lines.append(f"- {check_name(check)} [{state}]{suffix}")

    return "\n".join(lines)


def render_comments(comments: list[dict[str, Any]]) -> str:
    if not comments:
        return "_No comments._\n"

    parts: list[str] = []
    for comment in comments:
        author = author_login(comment)
        created = comment.get("createdAt", "")
        body = comment.get("body") or ""
        parts.append(f"### Comment by {author} at {created}\n\n{body}\n")

    return "\n".join(parts)


def render_metadata_lines(lines: list[tuple[str, Any]]) -> str:
    return "\n".join(f"- {label}: {value}" for label, value in lines)


def render_changed_files(files: list[dict[str, Any]]) -> str:
    lines = []
    for changed_file in files:
        path = changed_file.get("path", "")
        additions = changed_file.get("additions", 0)
        deletions = changed_file.get("deletions", 0)
        lines.append(f"- `{path}` (+{additions}/-{deletions})")

    return "\n".join(lines) if lines else "_No files listed._"


def render_commit_list(commits: list[dict[str, Any]]) -> str:
    lines = []
    for commit in commits:
        message = commit.get("messageHeadline") or commit.get("message") or ""
        oid = commit.get("oid", "")[:12]
        lines.append(f"- `{oid}` {message}")

    return "\n".join(lines) if lines else "_No commits listed._"


def render_issue(issue: dict[str, Any]) -> str:
    body = issue.get("body") or ""
    comments = issue.get("comments") or []
    metadata = render_metadata_lines(
        [
            ("State", issue.get("state")),
            ("Author", author_login(issue)),
            ("Labels", label_names(issue)),
            ("Created", issue.get("createdAt")),
            ("Updated", issue.get("updatedAt")),
            ("URL", issue.get("url")),
        ]
    )

    return f"""# Issue #{issue["number"]}: {issue["title"]}

{metadata}

## Body

{body}

## Comments

{render_comments(comments)}
"""


def render_pr(pr: dict[str, Any]) -> str:
    body = pr.get("body") or ""
    files = pr.get("files") or []
    comments = pr.get("comments") or []
    commits = pr.get("commits") or []
    status_check_rollup = pr.get("statusCheckRollup") or []
    metadata = render_metadata_lines(
        [
            ("State", pr.get("state")),
            ("Draft", pr.get("isDraft")),
            ("Author", author_login(pr)),
            ("Labels", label_names(pr)),
            ("Base", pr.get("baseRefName")),
            ("Head", pr.get("headRefName")),
            ("Mergeable", pr.get("mergeable")),
            ("Review decision", pr.get("reviewDecision")),
            ("Created", pr.get("createdAt")),
            ("Updated", pr.get("updatedAt")),
            ("URL", pr.get("url")),
        ]
    )

    return f"""# Pull Request #{pr["number"]}: {pr["title"]}

{metadata}

## Body

{body}

## Changed files

{render_changed_files(files)}

## Commits

{render_commit_list(commits)}

## CI status

{render_ci_status(status_check_rollup)}

## Comments

{render_comments(comments)}
"""


def render_issues_index(issues: list[dict[str, Any]]) -> str:
    lines = ["# Issues Index", ""]

    for issue in sorted(issues, key=lambda item: item.get("updatedAt", ""), reverse=True):
        lines.append(
            f"- {issue_identity(issue)}: {issue['title']} "
            f"[state: {issue.get('state')}] "
            f"[labels: {label_names(issue)}] "
            f"[updated: {issue.get('updatedAt')}]"
        )

    return "\n".join(lines) + "\n"


def render_prs_index(prs: list[dict[str, Any]]) -> str:
    lines = ["# Pull Requests Index", ""]

    for pr in sorted(prs, key=lambda item: item.get("updatedAt", ""), reverse=True):
        lines.append(
            f"- #{pr['number']}: {pr['title']} "
            f"[state: {pr.get('state')}] "
            f"[draft: {pr.get('isDraft')}] "
            f"[review: {pr.get('reviewDecision')}] "
            f"[mergeable: {pr.get('mergeable')}] "
            f"[ci: {ci_summary(pr.get('statusCheckRollup') or [])}] "
            f"[updated: {pr.get('updatedAt')}]"
        )

    return "\n".join(lines) + "\n"


def render_branches_ahead(data: dict[str, Any]) -> str:
    branches = data.get("branches") or []
    base_branch = data.get("baseBranch") or "master"
    lines = [
        f"# Branches Ahead Of {base_branch}",
        "",
        f"- Repository: {data.get('repo')}",
        f"- Remote: {data.get('remote') or 'unknown'}",
        f"- Base branch: {base_branch}",
        f"- Base branch source: {data.get('baseBranchSource') or 'unknown'}",
        "",
    ]

    warning = data.get("warning")
    if warning:
        lines.append(f"Warning: {warning}")
        lines.append("")

    if not branches:
        lines.append(f"_No branches ahead of {base_branch} found from local remote refs._")
        return "\n".join(lines) + "\n"

    for branch in branches:
        lines.append(
            f"## {branch.get('name')} "
            f"[ahead: {branch.get('aheadBy')}] "
            f"[behind: {branch.get('behindBy')}]"
        )
        lines.append("")

        commits = branch.get("commits") or []
        for commit in commits:
            lines.append(
                f"- `{commit.get('shortOid')}` {commit.get('subject')} "
                f"[{commit.get('authoredAt')}, {commit.get('authorName')}]"
            )

        if not commits:
            lines.append("_No commit details listed._")

        lines.append("")

    return "\n".join(lines)
