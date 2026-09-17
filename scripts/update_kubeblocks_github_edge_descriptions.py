#!/usr/bin/env python3
"""Replace the placeholder edge-type descriptions
import_kubeblocks_github_dataset.py registered ("KubeBlocks GitHub pilot: X
edge type.") with real, human-readable descriptions of what each relation
means.

This is the data-side half of making reasoning conclusions readable: the
conclusion generator (aletheia/interfaces/api/repositories/reasoning/
traversal.py's _business_conclusion_from_traversal) reads each relation's
approved edge-type description (via reasoning_link_config) and, when
present, weaves it into the conclusion sentence instead of the bare
relation label -- so what a tenant curates here is what shows up in
findings. No relation vocabulary is hardcoded in that shared code; it all
comes from here.

propose_edge_type upserts by (project_id, canonical_key) -- re-running this
against an existing approved edge type just updates its description in
place, no re-review needed (see aletheia/ontology/store.py's
upsert_artifact).

Run: python scripts/update_kubeblocks_github_edge_descriptions.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from aletheia.core.tenant_registry import default_metadata_db_url  # noqa: E402
from aletheia.ontology.registry import propose_edge_type  # noqa: E402
from aletheia.ontology.store import ensure_artifact_schema  # noqa: E402

DEFAULT_TENANT_ID = "kubeblocks-github-v1"
EVIDENCE_REF = "kubeblocks_github_api"

# (domain, range, description) -- domain/range copied verbatim from
# import_kubeblocks_github_dataset.py's EDGE_TYPES so this only changes the
# description, not the schema.
EDGE_TYPES: dict[str, tuple[list[str], list[str], str]] = {
    "AUTHORED": (["User"], ["Issue", "PullRequest", "Commit"], "This user wrote the commit, or opened the issue/pull request."),
    "CLOSES": (["PullRequest"], ["Issue"], "Merging this pull request closes the issue."),
    "MERGES": (["PullRequest"], ["Commit"], "This pull request was merged via this commit."),
    "PARENT_COMMIT": (["Commit"], ["Commit"], "This commit's direct parent in the git history -- the commit it was made on top of."),
    "LABELED_WITH": (["Issue", "PullRequest"], ["Label"], "This issue or pull request carries this label."),
    "ASSIGNED_TO": (["Issue", "PullRequest"], ["User"], "This issue or pull request is assigned to this user."),
    "REVIEW_REQUESTED": (["PullRequest"], ["User"], "This pull request requested a code review from this user."),
    "TOUCHES": (["Commit"], ["File"], "This commit modifies this file."),
    "BELONGS_TO": (["Issue", "PullRequest", "Commit"], ["Repository"], "This issue, pull request, or commit belongs to this repository."),
}


def update_edge_descriptions(session, tenant_id: str) -> None:
    for name, (domain, rng, description) in EDGE_TYPES.items():
        propose_edge_type(
            session, tenant_id=tenant_id, name=name, domain=domain, range=rng,
            description=description,
            properties=[], confidence=1.0, evidence=[EVIDENCE_REF], status="approved",
        )
    session.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    args = parser.parse_args()

    metadata_db_url = default_metadata_db_url()
    engine = create_engine(metadata_db_url)
    ensure_artifact_schema(engine)
    session = sessionmaker(bind=engine)()

    print(f"Updating {len(EDGE_TYPES)} edge-type descriptions for tenant {args.tenant_id!r} ...")
    update_edge_descriptions(session, args.tenant_id)
    for name, (_, _, description) in EDGE_TYPES.items():
        print(f"  {name}: {description}")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
