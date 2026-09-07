#!/usr/bin/env python3
"""Pull a pilot slice of apecloud/kubeblocks GitHub activity (issues, pull
requests, and the commits merged PRs touched) into local JSON files, as raw
input for scripts/import_kubeblocks_github_dataset.py.

Uses the `gh` CLI (already authenticated in this environment) rather than a
raw HTTP client -- `gh api --paginate` handles GitHub's pagination and auth
headers for us.

Pilot scope (small, not full repo history): the most recently updated
--issue-limit issues and --pr-limit pull requests, plus one commit-detail
fetch per merged PR (to get the file-change paths a later Datalog
"affects"-propagation demo needs).

Run: python scripts/fetch_kubeblocks_github_data.py
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "datasets" / "kubeblocks_github"
DEFAULT_REPO = "apecloud/kubeblocks"

# Matches "Fixes #123", "closes #123, #124", "Resolved #123" etc. -- the
# GitHub-recognized closing-keyword convention. Case-insensitive; captures
# just the issue number.
_CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*#(\d+)", re.IGNORECASE)


def _gh_api(path: str) -> Any:
    result = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def fetch_pull_requests(repo: str, limit: int) -> list[dict[str, Any]]:
    """Manual page-by-page fetch with an early stop once `limit` is reached
    -- NOT gh's --paginate (which walks every page in the repo's history
    before we ever get to slice locally, defeating the pilot-scale intent).

    state=closed (not "all"): "most recently updated" skews heavily toward
    still-open PRs (comments/CI reruns keep bumping updated_at), which have
    no merge_commit_sha yet -- exactly the data the causal-chain/timeline
    and Datalog file-touch-propagation reasoning demos need. Closed also
    covers merged PRs (state="closed" with merged_at set)."""
    out: list[dict[str, Any]] = []
    page = 1
    while len(out) < limit:
        batch = _gh_api(f"repos/{repo}/pulls?state=closed&per_page=100&sort=updated&direction=desc&page={page}")
        if not batch:
            break
        out.extend(batch)
        page += 1
        if page > 20:  # safety cap regardless of --pr-limit
            break
    return out[:limit]


def fetch_issues(repo: str, limit: int) -> list[dict[str, Any]]:
    """The /issues endpoint returns PRs too (marked with a `pull_request`
    key) -- filter those out to get pure issues."""
    out: list[dict[str, Any]] = []
    page = 1
    while len(out) < limit:
        batch = _gh_api(f"repos/{repo}/issues?state=all&per_page=100&sort=updated&direction=desc&page={page}")
        if not batch:
            break
        out.extend(item for item in batch if "pull_request" not in item)
        page += 1
        if page > 20:  # safety cap regardless of --issue-limit
            break
    return out[:limit]


def fetch_commit_detail(repo: str, sha: str) -> dict[str, Any] | None:
    try:
        return _gh_api(f"repos/{repo}/commits/{sha}")
    except subprocess.CalledProcessError:
        return None


def extract_closes_numbers(*texts: str | None) -> list[int]:
    numbers: set[int] = set()
    for text in texts:
        if not text:
            continue
        numbers.update(int(n) for n in _CLOSES_RE.findall(text))
    return sorted(numbers)


def simplify_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
        return None
    return {"login": user.get("login"), "id": user.get("id"), "type": user.get("type")}


def simplify_label(label: dict[str, Any]) -> dict[str, Any]:
    return {"name": label.get("name"), "color": label.get("color")}


def simplify_pr(pr: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": pr["number"],
        "title": pr.get("title"),
        "state": pr.get("state"),
        "user": simplify_user(pr.get("user")),
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
        "closed_at": pr.get("closed_at"),
        "merged_at": pr.get("merged_at"),
        "merge_commit_sha": pr.get("merge_commit_sha"),
        "base_ref": (pr.get("base") or {}).get("ref"),
        "labels": [simplify_label(label) for label in pr.get("labels") or []],
        "assignees": [simplify_user(a) for a in pr.get("assignees") or []],
        "requested_reviewers": [simplify_user(r) for r in pr.get("requested_reviewers") or []],
        "closes_issue_numbers": extract_closes_numbers(pr.get("title"), pr.get("body")),
        "body_excerpt": (pr.get("body") or "")[:500],
    }


def simplify_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": issue["number"],
        "title": issue.get("title"),
        "state": issue.get("state"),
        "user": simplify_user(issue.get("user")),
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "closed_at": issue.get("closed_at"),
        "labels": [simplify_label(label) for label in issue.get("labels") or []],
        "assignees": [simplify_user(a) for a in issue.get("assignees") or []],
        "comments": issue.get("comments"),
    }


def simplify_commit(commit: dict[str, Any]) -> dict[str, Any]:
    author = commit.get("author") or {}
    commit_author = (commit.get("commit") or {}).get("author") or {}
    return {
        "sha": commit["sha"],
        "message": ((commit.get("commit") or {}).get("message") or "").splitlines()[0][:300],
        "author_login": author.get("login"),
        "author_name": commit_author.get("name"),
        "authored_at": commit_author.get("date"),
        "parents": [p.get("sha") for p in commit.get("parents") or []],
        "files": [
            {"filename": f.get("filename"), "status": f.get("status")}
            for f in commit.get("files") or []
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--issue-limit", type=int, default=300)
    parser.add_argument("--pr-limit", type=int, default=300)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    print(f"Fetching up to {args.pr_limit} pull requests from {args.repo} ...")
    raw_prs = fetch_pull_requests(args.repo, args.pr_limit)
    prs = [simplify_pr(pr) for pr in raw_prs]
    print(f"  got {len(prs)} pull requests")

    print(f"Fetching up to {args.issue_limit} issues from {args.repo} ...")
    raw_issues = fetch_issues(args.repo, args.issue_limit)
    issues = [simplify_issue(issue) for issue in raw_issues]
    print(f"  got {len(issues)} issues")

    merged_shas = sorted({pr["merge_commit_sha"] for pr in prs if pr.get("merged_at") and pr.get("merge_commit_sha")})
    print(f"Fetching commit detail (incl. file changes) for {len(merged_shas)} merge commits ...")
    commits: list[dict[str, Any]] = []
    for i, sha in enumerate(merged_shas, 1):
        detail = fetch_commit_detail(args.repo, sha)
        if detail:
            commits.append(simplify_commit(detail))
        if i % 50 == 0:
            print(f"  ... {i}/{len(merged_shas)}")
    print(f"  got {len(commits)} commit details")

    users: dict[str, dict[str, Any]] = {}
    for source in (prs, issues):
        for item in source:
            for key in ("user", "assignees", "requested_reviewers"):
                value = item.get(key)
                candidates = value if isinstance(value, list) else [value]
                for u in candidates:
                    if u and u.get("login"):
                        users.setdefault(u["login"], u)
    for c in commits:
        if c.get("author_login"):
            users.setdefault(c["author_login"], {"login": c["author_login"], "id": None, "type": "User"})
    print(f"Collected {len(users)} distinct users")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "pull_requests.json").write_text(json.dumps(prs, indent=2, ensure_ascii=False))
    (args.out_dir / "issues.json").write_text(json.dumps(issues, indent=2, ensure_ascii=False))
    (args.out_dir / "commits.json").write_text(json.dumps(commits, indent=2, ensure_ascii=False))
    (args.out_dir / "users.json").write_text(json.dumps(list(users.values()), indent=2, ensure_ascii=False))
    print(f"Wrote pull_requests.json, issues.json, commits.json, users.json to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
