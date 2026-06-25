import re
from typing import Any
from urllib.parse import unquote


def normalize_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def concrete_object_quality(record: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    label = str(record.get("label") or "").strip()
    object_type = str(record.get("class_label") or "").strip()
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", label)
    unique_token_count = len({token.lower() for token in tokens})
    issues = []
    score = 100.0
    if not label:
        issues.append({"code": "missing_label", "reason": "Concrete object has no stable label."})
        score -= 100
    if len(tokens) <= 2 and [token.lower() for token in tokens[:1]] in (["the"], ["a"], ["an"]):
        issues.append({"code": "short_determiner_phrase", "reason": "Label is a short determiner phrase, not a stable referential object."})
        score -= 35
    if re.search(r"\b[A-Za-z0-9-]+'s\b", label) and unique_token_count >= 4:
        issues.append({"code": "possessive_attribute_phrase", "reason": "Label is a possessive attribute phrase rather than a named object."})
        score -= 25
    if object_type:
        normalized_label = normalize_label(label)
        normalized_type = normalize_label(object_type)
        type_token_count = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", re.sub(r"([a-z])([A-Z])", r"\1 \2", object_type)))
        if normalized_type and (
            normalized_type == normalized_label
            or (type_token_count > 1 and normalized_label.endswith(normalized_type))
        ):
            issues.append({"code": "self_typed_label", "reason": "Object type is a direct restatement of the label instead of a reusable class."})
            score -= 30
    has_acronym_hint = bool(re.search(r"\([A-Z0-9]{2,}\)", label))
    if unique_token_count > 6 and not has_acronym_hint:
        issues.append({"code": "overlong_unreferential_label", "reason": "Label is an overlong phrase with no acronym or compact identity hint."})
        score -= min((unique_token_count - 6) * 8, 32)
    source_issue = source_query_quality_issue(item.get("source_url"))
    if source_issue:
        issues.append(source_issue)
        score -= 35
    return {"score": round(score, 3), "issues": issues}


def source_query_quality_issue(source_url: Any) -> dict[str, str] | None:
    value = str(source_url or "").strip()
    if not value.startswith("gpt_researcher://report/"):
        return None
    query_text = unquote(value.rsplit("/", 1)[-1]).replace("_", " ").replace("-", " ")
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9]*", query_text)
    if len(tokens) < 4:
        return None
    lowered = [token.lower() for token in tokens]
    for index in range(len(lowered) - 2):
        left, middle, right = lowered[index], tokens[index + 1], lowered[index + 2]
        if left == right and re.fullmatch(r"[A-Z0-9]{2,5}", middle):
            return {
                "code": "polluted_research_query",
                "reason": "Source report query contains repeated schema/type tokens around a code-like token.",
            }
    return None
