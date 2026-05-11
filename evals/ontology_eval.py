import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_GOLDEN = Path(__file__).parent / "fixtures" / "northwind_golden.json"
DEFAULT_OPTIONAL = Path(__file__).parent / "fixtures" / "northwind_optional.json"


def normalize_name(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value or "")
    return re.sub(r"[^a-z0-9]+", "", value.lower())


@dataclass(frozen=True)
class OntologyObject:
    name: str
    tables: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return normalize_name(self.name)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "tables": list(self.tables)}


@dataclass(frozen=True)
class OntologyLink:
    source: str
    target: str
    link_type: str = ""

    @property
    def key(self) -> str:
        # Northwind v1 evaluates relationship coverage, not direction naming.
        return "--".join(sorted((normalize_name(self.source), normalize_name(self.target))))

    def as_dict(self) -> dict[str, Any]:
        data = {"source": self.source, "target": self.target}
        if self.link_type:
            data["link_type"] = self.link_type
        return data


@dataclass(frozen=True)
class OntologySnapshot:
    objects: tuple[OntologyObject, ...]
    links: tuple[OntologyLink, ...]

    @property
    def object_keys(self) -> set[str]:
        return {obj.key for obj in self.objects}

    @property
    def link_keys(self) -> set[str]:
        return {link.key for link in self.links}


def _first_present(record: dict[str, Any], names: Iterable[str], default: Any = None) -> Any:
    for name in names:
        if name in record and record[name] not in (None, ""):
            return record[name]
    return default


def _extract_objects(payload: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = _extract_artifacts(payload, "object")
    if artifacts:
        return [
            {
                "name": _first_present(artifact, ("name",)) or _name_from_canonical_key(artifact.get("canonical_key", "")),
                "tables": _first_present(_artifact_payload(artifact), ("tables", "mapped_tables", "mapped_table_names"), []),
            }
            for artifact in artifacts
        ]
    if isinstance(payload.get("objects"), list):
        return payload["objects"]
    if isinstance(payload.get("business_objects"), list):
        return payload["business_objects"]
    return []


def _extract_links(payload: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = _extract_artifacts(payload, "link")
    if artifacts:
        links = []
        for artifact in artifacts:
            artifact_payload = _artifact_payload(artifact)
            source, target = _link_from_canonical_key(artifact.get("canonical_key", ""))
            links.append(
                {
                    "source": _first_present(artifact_payload, ("source", "source_object", "source_object_name", "source_name"), source),
                    "target": _first_present(artifact_payload, ("target", "target_object", "target_object_name", "target_name"), target),
                    "link_type": _first_present(artifact_payload, ("link_type", "cardinality", "relationship_type", "type"), ""),
                }
            )
        return links
    if isinstance(payload.get("links"), list):
        return payload["links"]
    if isinstance(payload.get("business_links"), list):
        return payload["business_links"]
    return []


def _extract_artifacts(payload: dict[str, Any], artifact_type: str) -> list[dict[str, Any]]:
    artifacts = payload.get("artifacts") or payload.get("ontology_artifacts") or []
    if not isinstance(artifacts, list):
        return []
    return [
        artifact
        for artifact in artifacts
        if str(_first_present(artifact, ("artifact_type", "type"), "")).lower() == artifact_type
    ]


def _artifact_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    payload = artifact.get("payload_json") or artifact.get("payload") or {}
    return payload if isinstance(payload, dict) else {}


def _name_from_canonical_key(canonical_key: str) -> str:
    parts = [part for part in str(canonical_key).split(":") if part]
    return parts[-1] if parts else ""


def _link_from_canonical_key(canonical_key: str) -> tuple[str, str]:
    parts = [part for part in str(canonical_key).split(":") if part]
    if len(parts) >= 3 and parts[0] == "link":
        return parts[1], parts[-1]
    return "", ""


def load_snapshot(path: Path) -> OntologySnapshot:
    payload = json.loads(path.read_text(encoding="utf-8"))

    objects: list[OntologyObject] = []
    for item in _extract_objects(payload):
        name = _first_present(item, ("name", "object_name", "business_object", "label"))
        if not name:
            continue
        tables = _first_present(
            item,
            ("tables", "mapped_table_names", "underlying_tables", "source_tables"),
            [],
        )
        objects.append(OntologyObject(str(name), tuple(str(table) for table in tables)))

    links: list[OntologyLink] = []
    for item in _extract_links(payload):
        source = _first_present(item, ("source", "source_object_name", "source_name"))
        target = _first_present(item, ("target", "target_object_name", "target_name"))
        if not source or not target:
            continue
        links.append(
            OntologyLink(
                str(source),
                str(target),
                str(_first_present(item, ("link_type", "relationship_type", "type"), "")),
            )
        )

    return OntologySnapshot(tuple(objects), tuple(links))


def _items_by_key(items: Iterable[OntologyObject | OntologyLink]) -> dict[str, Any]:
    return {item.key: item.as_dict() for item in items}


def compare_item_set(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    expected_keys = set(expected)
    actual_keys = set(actual)
    matched = expected_keys & actual_keys
    extra = actual_keys - expected_keys
    missing = expected_keys - actual_keys
    precision = len(matched) / len(actual_keys) if actual_keys else (1.0 if not expected_keys else 0.0)
    recall = len(matched) / len(expected_keys) if expected_keys else 1.0
    return {
        "expected_count": len(expected_keys),
        "actual_count": len(actual_keys),
        "matched_count": len(matched),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "missing": [expected[key] for key in sorted(missing)],
        "extra": [actual[key] for key in sorted(extra)],
    }


def evaluate(golden: OntologySnapshot, actual: OntologySnapshot) -> dict[str, Any]:
    return {
        "objects": compare_item_set(_items_by_key(golden.objects), _items_by_key(actual.objects)),
        "links": compare_item_set(_items_by_key(golden.links), _items_by_key(actual.links)),
    }


def evaluate_required_optional(
    required: OntologySnapshot,
    optional: OntologySnapshot,
    actual: OntologySnapshot,
) -> dict[str, Any]:
    return {
        "objects": compare_required_optional_set(
            _items_by_key(required.objects),
            _items_by_key(optional.objects),
            _items_by_key(actual.objects),
        ),
        "links": compare_required_optional_set(
            _items_by_key(required.links),
            _items_by_key(optional.links),
            _items_by_key(actual.links),
        ),
    }


def compare_required_optional_set(
    required: dict[str, Any],
    optional: dict[str, Any],
    actual: dict[str, Any],
) -> dict[str, Any]:
    required_keys = set(required)
    optional_keys = set(optional) - required_keys
    actual_keys = set(actual)
    required_hits = required_keys & actual_keys
    optional_hits = optional_keys & actual_keys
    unexpected = actual_keys - required_keys - optional_keys
    required_recall = len(required_hits) / len(required_keys) if required_keys else 1.0
    optional_recall = len(optional_hits) / len(optional_keys) if optional_keys else 1.0
    return {
        "required_count": len(required_keys),
        "required_matched_count": len(required_hits),
        "required_recall": round(required_recall, 4),
        "required_missing": [required[key] for key in sorted(required_keys - actual_keys)],
        "optional_count": len(optional_keys),
        "optional_matched_count": len(optional_hits),
        "optional_recall": round(optional_recall, 4),
        "optional_missing": [optional[key] for key in sorted(optional_keys - actual_keys)],
        "optional_hit": [actual[key] for key in sorted(optional_hits)],
        "unexpected_extra": [actual[key] for key in sorted(unexpected)],
    }


def stability_diff(previous: OntologySnapshot | None, actual: OntologySnapshot) -> dict[str, Any] | None:
    if previous is None:
        return None
    previous_objects = _items_by_key(previous.objects)
    actual_objects = _items_by_key(actual.objects)
    previous_links = _items_by_key(previous.links)
    actual_links = _items_by_key(actual.links)
    return {
        "objects": {
            "added": [actual_objects[key] for key in sorted(set(actual_objects) - set(previous_objects))],
            "removed": [previous_objects[key] for key in sorted(set(previous_objects) - set(actual_objects))],
        },
        "links": {
            "added": [actual_links[key] for key in sorted(set(actual_links) - set(previous_links))],
            "removed": [previous_links[key] for key in sorted(set(previous_links) - set(actual_links))],
        },
    }


def build_report(
    golden_path: Path,
    actual_path: Path,
    previous_path: Path | None = None,
    optional_path: Path | None = None,
) -> dict[str, Any]:
    golden = load_snapshot(golden_path)
    actual = load_snapshot(actual_path)
    previous = load_snapshot(previous_path) if previous_path else None
    report = {
        "golden": str(golden_path),
        "actual": str(actual_path),
        "evaluation": evaluate(golden, actual),
        "stability_diff": stability_diff(previous, actual),
    }
    if optional_path:
        optional = load_snapshot(optional_path)
        report["optional_golden"] = str(optional_path)
        report["required_optional_evaluation"] = evaluate_required_optional(golden, optional, actual)
    return report


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Aletheia Ontology Eval Report",
        "",
        f"- Golden: `{report['golden']}`",
        f"- Actual: `{report['actual']}`",
        "",
    ]
    for section in ("objects", "links"):
        result = report["evaluation"][section]
        lines.extend(
            [
                f"## {section.title()}",
                "",
                f"- Expected: {result['expected_count']}",
                f"- Actual: {result['actual_count']}",
                f"- Matched: {result['matched_count']}",
                f"- Precision: {result['precision']}",
                f"- Recall: {result['recall']}",
                f"- Missing: {json.dumps(result['missing'], ensure_ascii=False)}",
                f"- Extra: {json.dumps(result['extra'], ensure_ascii=False)}",
                "",
            ]
        )

    if report["stability_diff"] is not None:
        lines.append("## Stability Diff")
        lines.append("")
        lines.append(f"```json\n{json.dumps(report['stability_diff'], indent=2, ensure_ascii=False)}\n```")
        lines.append("")

    if "required_optional_evaluation" in report:
        lines.extend(
            [
                "## Required / Optional",
                "",
                f"- Optional Golden: `{report['optional_golden']}`",
                "",
            ]
        )
        for section in ("objects", "links"):
            result = report["required_optional_evaluation"][section]
            lines.extend(
                [
                    f"### {section.title()}",
                    "",
                    f"- Required: {result['required_matched_count']} / {result['required_count']}",
                    f"- Required Recall: {result['required_recall']}",
                    f"- Required Missing: {json.dumps(result['required_missing'], ensure_ascii=False)}",
                    f"- Optional: {result['optional_matched_count']} / {result['optional_count']}",
                    f"- Optional Recall: {result['optional_recall']}",
                    f"- Optional Missing: {json.dumps(result['optional_missing'], ensure_ascii=False)}",
                    f"- Optional Hit: {json.dumps(result['optional_hit'], ensure_ascii=False)}",
                    f"- Unexpected Extra: {json.dumps(result['unexpected_extra'], ensure_ascii=False)}",
                    "",
                ]
            )

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate generated ontology objects and links.")
    parser.add_argument("--actual", required=True, type=Path, help="JSON file with generated objects and links.")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN, help="Golden ontology JSON.")
    parser.add_argument("--optional-golden", type=Path, help="Optional ontology JSON for required/optional reporting.")
    parser.add_argument("--previous", type=Path, help="Previous generated ontology JSON for stability diff.")
    parser.add_argument("--report-json", type=Path, help="Write machine-readable JSON report.")
    parser.add_argument("--report-md", type=Path, help="Write Markdown report.")
    args = parser.parse_args()

    report = build_report(args.golden, args.actual, args.previous, args.optional_golden)
    print(json.dumps(report, indent=2, ensure_ascii=False))

    if args.report_json:
        args.report_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.report_md:
        write_markdown_report(report, args.report_md)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
