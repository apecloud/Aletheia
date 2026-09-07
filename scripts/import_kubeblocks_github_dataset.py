#!/usr/bin/env python3
"""Materialize the KubeBlocks GitHub pilot dataset (scripts/fetch_kubeblocks_
github_data.py's output) into a graph-native Aletheia tenant.

Follows the same no-LLM, structured-data call sequence as
scripts/import_webqsp_graph_tenant.py: register node/edge types into the
ontology registry (status="approved" directly -- the precedented
"auto_approve" governance mode every scripted benchmark tenant uses, no
human-review round trip needed for a scripted import), sync that into Nebula
TAG/EDGE TYPE DDL, then insert vertex/edge rows.

All properties are declared data_type="string" -- NebulaGraphClient.
insert_vertices/insert_edges always quote every value as a string literal
regardless of the TAG's declared type (see aletheia/graph_store/
nebula_client.py), and every existing scripted importer (HotpotQA, WebQSP)
follows the same string-only convention rather than fighting that.

Run: python -m aletheia... no -- this is a scripts/ operational script:
    python scripts/import_kubeblocks_github_dataset.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from aletheia.core.tenant_registry import default_metadata_db_url  # noqa: E402
from aletheia.enrichment.schema_sync import sync_tenant_schema  # noqa: E402
from aletheia.graph_store.nebula_client import NebulaGraphClient, insert_with_schema_retry  # noqa: E402
from aletheia.ontology.registry import propose_edge_type, propose_node_type  # noqa: E402
from aletheia.ontology.store import ensure_artifact_schema  # noqa: E402

DEFAULT_TENANT_ID = "kubeblocks-github-v1"
DEFAULT_SPACE = "kubeblocks_kg"
DEFAULT_DATA_DIR = ROOT / "datasets" / "kubeblocks_github"
DEFAULT_REPO_SLUG = "apecloud/kubeblocks"

# Every property is "string" -- see module docstring for why.
NODE_TYPES: dict[str, list[str]] = {
    "Repository": ["name"],
    "Issue": ["number", "title", "state", "created_at", "closed_at"],
    "PullRequest": ["number", "title", "state", "created_at", "closed_at", "merged_at", "base_ref"],
    "Commit": ["sha", "message", "authored_at"],
    "User": ["login"],
    "Label": ["name"],
    "File": ["file_path"],
}

# name -> (domain, range)
EDGE_TYPES: dict[str, tuple[list[str], list[str]]] = {
    "AUTHORED": (["User"], ["Issue", "PullRequest", "Commit"]),
    "CLOSES": (["PullRequest"], ["Issue"]),
    "MERGES": (["PullRequest"], ["Commit"]),
    "PARENT_COMMIT": (["Commit"], ["Commit"]),
    "LABELED_WITH": (["Issue", "PullRequest"], ["Label"]),
    "ASSIGNED_TO": (["Issue", "PullRequest"], ["User"]),
    "REVIEW_REQUESTED": (["PullRequest"], ["User"]),
    "TOUCHES": (["Commit"], ["File"]),
    "BELONGS_TO": (["Issue", "PullRequest", "Commit"], ["Repository"]),
}


def repo_id(repo_slug: str) -> str:
    return f"kb_repo_{repo_slug.replace('/', '_')}"


def issue_id(number: int) -> str:
    return f"kb_issue_{number}"


def pr_id(number: int) -> str:
    return f"kb_pr_{number}"


def commit_id(sha: str) -> str:
    return f"kb_commit_{sha}"


def user_id(login: str) -> str:
    return f"kb_user_{login}"


def label_id(name: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in name)
    return f"kb_label_{safe}"


def file_id(path: str) -> str:
    # VIDs are FIXED_STRING(128); long generated/vendor paths can exceed
    # that, so hash-truncate anything long rather than silently corrupting
    # the insert. Short paths keep their real (readable) id.
    safe = "".join(c if c.isalnum() else "_" for c in path)
    if len(safe) <= 100:
        return f"kb_file_{safe}"
    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
    return f"kb_file_h{digest}"


def _s(value: Any) -> str:
    return "" if value is None else str(value)


class GraphBuilder:
    def __init__(self):
        self.nodes: dict[str, dict[str, dict]] = {t: {} for t in NODE_TYPES}
        self.edges: dict[str, list[dict]] = {t: [] for t in EDGE_TYPES}

    def add_node(self, node_type: str, node_id: str, **props):
        row = {"id": node_id}
        for field in NODE_TYPES[node_type]:
            row[field] = _s(props.get(field))
        self.nodes[node_type][node_id] = row  # de-dup by id, last write wins

    def add_edge(self, edge_type: str, source_id: str, target_id: str):
        self.edges[edge_type].append({"source_id": source_id, "target_id": target_id})

    def vertex_rows_by_type(self) -> dict[str, list[dict]]:
        return {t: list(rows.values()) for t, rows in self.nodes.items() if rows}

    def edge_rows_by_type(self) -> dict[str, list[dict]]:
        return {t: rows for t, rows in self.edges.items() if rows}


def build_graph(data_dir: Path, repo_slug: str) -> GraphBuilder:
    prs = json.loads((data_dir / "pull_requests.json").read_text())
    issues = json.loads((data_dir / "issues.json").read_text())
    commits = json.loads((data_dir / "commits.json").read_text())
    users = json.loads((data_dir / "users.json").read_text())

    g = GraphBuilder()
    repo_vid = repo_id(repo_slug)
    g.add_node("Repository", repo_vid, name=repo_slug)

    for u in users:
        if u.get("login"):
            g.add_node("User", user_id(u["login"]), login=u["login"])

    for issue in issues:
        vid = issue_id(issue["number"])
        g.add_node(
            "Issue", vid, number=issue["number"], title=issue.get("title"),
            state=issue.get("state"), created_at=issue.get("created_at"), closed_at=issue.get("closed_at"),
        )
        g.add_edge("BELONGS_TO", vid, repo_vid)
        author = (issue.get("user") or {}).get("login")
        if author:
            g.add_edge("AUTHORED", user_id(author), vid)
        for label in issue.get("labels") or []:
            if label.get("name"):
                lid = label_id(label["name"])
                g.add_node("Label", lid, name=label["name"])
                g.add_edge("LABELED_WITH", vid, lid)
        for assignee in issue.get("assignees") or []:
            if assignee and assignee.get("login"):
                g.add_edge("ASSIGNED_TO", vid, user_id(assignee["login"]))

    for pr in prs:
        vid = pr_id(pr["number"])
        g.add_node(
            "PullRequest", vid, number=pr["number"], title=pr.get("title"), state=pr.get("state"),
            created_at=pr.get("created_at"), closed_at=pr.get("closed_at"),
            merged_at=pr.get("merged_at"), base_ref=pr.get("base_ref"),
        )
        g.add_edge("BELONGS_TO", vid, repo_vid)
        author = (pr.get("user") or {}).get("login")
        if author:
            g.add_edge("AUTHORED", user_id(author), vid)
        for label in pr.get("labels") or []:
            if label.get("name"):
                lid = label_id(label["name"])
                g.add_node("Label", lid, name=label["name"])
                g.add_edge("LABELED_WITH", vid, lid)
        for assignee in pr.get("assignees") or []:
            if assignee and assignee.get("login"):
                g.add_edge("ASSIGNED_TO", vid, user_id(assignee["login"]))
        for reviewer in pr.get("requested_reviewers") or []:
            if reviewer and reviewer.get("login"):
                g.add_edge("REVIEW_REQUESTED", vid, user_id(reviewer["login"]))
        for closed_issue_number in pr.get("closes_issue_numbers") or []:
            g.add_edge("CLOSES", vid, issue_id(closed_issue_number))
        if pr.get("merged_at") and pr.get("merge_commit_sha"):
            g.add_edge("MERGES", vid, commit_id(pr["merge_commit_sha"]))

    for commit in commits:
        vid = commit_id(commit["sha"])
        g.add_node("Commit", vid, sha=commit["sha"], message=commit.get("message"), authored_at=commit.get("authored_at"))
        g.add_edge("BELONGS_TO", vid, repo_vid)
        if commit.get("author_login"):
            g.add_edge("AUTHORED", user_id(commit["author_login"]), vid)
        for parent_sha in commit.get("parents") or []:
            g.add_edge("PARENT_COMMIT", vid, commit_id(parent_sha))
        for f in commit.get("files") or []:
            path = f.get("filename")
            if path:
                fid = file_id(path)
                g.add_node("File", fid, file_path=path)
                g.add_edge("TOUCHES", vid, fid)

    return g


def register_ontology(session, tenant_id: str) -> None:
    for name, fields in NODE_TYPES.items():
        propose_node_type(
            session, tenant_id=tenant_id, name=name,
            description=f"KubeBlocks GitHub pilot: {name} node type.",
            properties=[{"name": f, "data_type": "string"} for f in fields],
            confidence=1.0, evidence=["kubeblocks_github_api"], status="approved",
        )
    for name, (domain, rng) in EDGE_TYPES.items():
        propose_edge_type(
            session, tenant_id=tenant_id, name=name, domain=domain, range=rng,
            description=f"KubeBlocks GitHub pilot: {name} edge type.",
            properties=[], confidence=1.0, evidence=["kubeblocks_github_api"], status="approved",
        )
    session.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    parser.add_argument("--space", default=DEFAULT_SPACE)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--repo-slug", default=DEFAULT_REPO_SLUG)
    parser.add_argument("--nebula-ip", default="127.0.0.1")
    parser.add_argument("--nebula-port", type=int, default=9669)
    parser.add_argument("--nebula-user", default="root")
    parser.add_argument("--nebula-pass", default="nebula")
    args = parser.parse_args()

    metadata_db_url = default_metadata_db_url()
    engine = create_engine(metadata_db_url)
    ensure_artifact_schema(engine)
    session = sessionmaker(bind=engine)()

    print(f"Registering {len(NODE_TYPES)} node types + {len(EDGE_TYPES)} edge types for tenant {args.tenant_id!r} ...")
    register_ontology(session, args.tenant_id)

    print(f"Building graph from {args.data_dir} ...")
    graph = build_graph(args.data_dir, args.repo_slug)
    vertex_rows_by_type = graph.vertex_rows_by_type()
    edge_rows_by_type = graph.edge_rows_by_type()
    for t, rows in vertex_rows_by_type.items():
        print(f"  {t}: {len(rows)} nodes")
    for t, rows in edge_rows_by_type.items():
        print(f"  {t}: {len(rows)} edges")

    client = NebulaGraphClient(
        ip=args.nebula_ip, port=args.nebula_port, user=args.nebula_user,
        password=args.nebula_pass, space=args.space,
    )
    client.connect()
    try:
        client.execute_query(
            f"CREATE SPACE IF NOT EXISTS {args.space} (partition_num=1, replica_factor=1, vid_type=FIXED_STRING(128));"
        )
        print("Syncing tenant schema (TAG/EDGE DDL) into Nebula ...")
        sync_tenant_schema(session, client, args.tenant_id)
        print("Inserting vertices ...")
        for node_type, rows in vertex_rows_by_type.items():
            insert_with_schema_retry(lambda t=node_type, r=rows: client.insert_vertices(t, r))
        print("Inserting edges ...")
        for edge_type, rows in edge_rows_by_type.items():
            insert_with_schema_retry(lambda t=edge_type, r=rows: client.insert_edges(t, r))
    finally:
        client.close()

    print(f"Done. Tenant {args.tenant_id!r} / space {args.space!r} ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
