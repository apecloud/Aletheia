import argparse
import hashlib
import html
import ipaddress
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ontology_artifacts import (
    ArtifactEvidence,
    OntologyArtifact,
    WebEnrichmentProposal,
    WebEnrichmentRun,
    _content_hash,
    _json_dump,
    canonical_key_for,
    ensure_artifact_schema,
    replace_evidence,
    upsert_artifact,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("WebEnrichmentAgent")


BLOCKED_URL_PATTERNS = [
    re.compile(r"(^|[?&])(api[_-]?key|token|secret|password|credential)=", re.I),
]


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _text_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _clean_text(value: str | None, limit: int = 1200) -> str:
    if not value:
        return ""
    text = html.unescape(re.sub(r"\s+", " ", value)).strip()
    return text[:limit].rstrip()


def _domain_matches(hostname: str, allowed_domains: set[str]) -> bool:
    host = hostname.lower().strip(".")
    return any(host == domain or host.endswith("." + domain) for domain in allowed_domains)


def _robots_txt_url(source_url: str) -> str:
    parsed = urlparse(source_url)
    return f"{parsed.scheme}://{parsed.netloc}/robots.txt"


def _is_public_web_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    if any(pattern.search(url) for pattern in BLOCKED_URL_PATTERNS):
        return False
    host = parsed.hostname
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return not (host in {"localhost"} or host.endswith(".local"))
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _is_crawl_allowed(url: str, allowed_domains: set[str], allow_discovered_domains: bool) -> tuple[bool, str | None]:
    if not _is_public_web_url(url):
        return False, "blocked_non_public_or_sensitive_url"
    hostname = (urlparse(url).hostname or "").lower()
    if allowed_domains and not _domain_matches(hostname, allowed_domains):
        return False, "blocked_domain_not_allowlisted"
    if not allowed_domains and not allow_discovered_domains:
        return False, "crawl_requires_allowlist_or_allow_discovered_domains"
    return True, None


class TextExtractingHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self._in_title = False
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
            return
        text = data.strip()
        if text:
            self.parts.append(text)

    def extracted_text(self, limit: int) -> str:
        return _clean_text(" ".join(self.parts), limit)


@dataclass
class SearchResult:
    query: str
    title: str
    url: str
    snippet: str = ""
    rank: int = 0
    provider: str = "static"


class StaticSearchProvider:
    def __init__(self, path: str | None = None, seed_urls: list[str] | None = None):
        self.path = path
        self.seed_urls = seed_urls or []
        self.results_by_query: dict[str, list[dict[str, Any]]] = {}
        self.all_results: list[dict[str, Any]] = []
        if path:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if key in {"results", "items"} and isinstance(value, list):
                        self.all_results.extend(value)
                    elif isinstance(value, list):
                        self.results_by_query[key] = value
            elif isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict) and "query" in item and isinstance(item.get("results"), list):
                        self.results_by_query[item["query"]] = item["results"]
                    elif isinstance(item, dict):
                        self.all_results.append(item)

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        raw_results = self.results_by_query.get(query) or self.all_results
        out: list[SearchResult] = []
        for idx, item in enumerate(raw_results[:max_results], 1):
            url = item.get("url") or item.get("href")
            if not url:
                continue
            out.append(
                SearchResult(
                    query=query,
                    title=_clean_text(item.get("title") or url, 220),
                    url=url,
                    snippet=_clean_text(item.get("snippet") or item.get("summary") or "", 500),
                    rank=idx,
                    provider="static_json",
                )
            )
        if self.seed_urls:
            start = len(out) + 1
            for offset, url in enumerate(self.seed_urls[: max(0, max_results - len(out))], 0):
                out.append(SearchResult(query=query, title=url, url=url, rank=start + offset, provider="seed_url"))
        return out[:max_results]


class DuckDuckGoHTMLSearchProvider:
    def __init__(self, timeout_seconds: float = 8.0):
        self.timeout_seconds = timeout_seconds

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        response = requests.get(
            "https://duckduckgo.com/html/",
            params={"q": query},
            timeout=self.timeout_seconds,
            headers={"User-Agent": "AletheiaWebEnrichment/0.1 (+https://local.invalid)"},
        )
        response.raise_for_status()
        results: list[SearchResult] = []
        pattern = re.compile(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            re.I | re.S,
        )
        for idx, (href, title_html) in enumerate(pattern.findall(response.text), 1):
            url = html.unescape(href)
            if "duckduckgo.com/l/?" in url:
                qs = parse_qs(urlparse(url).query)
                if qs.get("uddg"):
                    url = unquote(qs["uddg"][0])
            title = _clean_text(re.sub(r"<[^>]+>", " ", title_html), 220)
            results.append(SearchResult(query=query, title=title or url, url=url, rank=idx, provider="duckduckgo_html"))
            if len(results) >= max_results:
                break
        return results


class WebEnrichmentAgent:
    def __init__(
        self,
        metadata_db_url: str,
        tenant: str = "default",
        *,
        search_results_json: str | None = None,
        seed_urls: list[str] | None = None,
        enable_live_search: bool = False,
        allowed_domains: list[str] | None = None,
        allow_discovered_domains: bool = False,
        timeout_seconds: float = 8.0,
        max_artifacts: int = 5,
        max_results_per_query: int = 3,
        max_crawl_pages: int = 2,
        max_page_bytes: int = 256_000,
    ):
        self.metadata_engine = create_engine(metadata_db_url)
        ensure_artifact_schema(self.metadata_engine)
        self.Session = sessionmaker(bind=self.metadata_engine)
        self.tenant = tenant
        self.search_results_json = search_results_json
        self.seed_urls = seed_urls or []
        self.enable_live_search = enable_live_search
        self.allowed_domains = {d.lower().strip() for d in (allowed_domains or []) if d.strip()}
        self.allow_discovered_domains = allow_discovered_domains
        self.timeout_seconds = timeout_seconds
        self.max_artifacts = max_artifacts
        self.max_results_per_query = max_results_per_query
        self.max_crawl_pages = max_crawl_pages
        self.max_page_bytes = max_page_bytes
        if enable_live_search:
            self.provider = DuckDuckGoHTMLSearchProvider(timeout_seconds=timeout_seconds)
        else:
            self.provider = StaticSearchProvider(search_results_json, self.seed_urls)

    def _search_provider_name(self) -> str:
        if self.enable_live_search:
            return "duckduckgo_html"
        if self.search_results_json:
            return "static_json"
        if self.seed_urls:
            return "seed_url"
        return "offline_empty"

    def _fetch_target_artifacts(self, session, artifact_keys: list[str] | None, statuses: set[str]) -> list[OntologyArtifact]:
        query = (
            session.query(OntologyArtifact)
            .filter(OntologyArtifact.project_id == self.tenant)
            .filter(OntologyArtifact.artifact_type.notin_(["WebEnrichment", "web_enrichment"]))
            .order_by(OntologyArtifact.updated_at.desc(), OntologyArtifact.canonical_key.asc())
        )
        if artifact_keys:
            query = query.filter(OntologyArtifact.canonical_key.in_(artifact_keys))
        if statuses:
            query = query.filter(OntologyArtifact.status.in_(statuses))
        return query.limit(self.max_artifacts).all()

    def _query_for_artifact(self, artifact: OntologyArtifact) -> str:
        payload = _json_load(artifact.payload_json, {})
        parts = [artifact.name, artifact.description or ""]
        for key in (
            "name",
            "description",
            "source_object_name",
            "target_object_name",
            "link_type",
        ):
            value = payload.get(key)
            if value:
                parts.append(str(value))
        compact = " ".join(_clean_text(part, 120) for part in parts if part)
        return f"{compact} ontology definition source evidence".strip()

    def _crawl_result(self, result: SearchResult) -> tuple[dict[str, Any], str | None]:
        allowed, blocked_reason = _is_crawl_allowed(result.url, self.allowed_domains, self.allow_discovered_domains)
        if not allowed:
            return {
                "title": result.title,
                "text": "",
                "used_snippet_only": True,
                "blocked_reason": blocked_reason,
            }, blocked_reason
        response = requests.get(
            result.url,
            timeout=self.timeout_seconds,
            headers={"User-Agent": "AletheiaWebEnrichment/0.1 (+https://local.invalid)"},
            stream=True,
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        raw = response.raw.read(self.max_page_bytes + 1, decode_content=True)
        truncated = len(raw) > self.max_page_bytes
        raw = raw[: self.max_page_bytes]
        text = raw.decode(response.encoding or "utf-8", errors="replace")
        parsed_title = result.title
        body_text = ""
        if "html" in content_type or "<html" in text[:1000].lower():
            parser = TextExtractingHTMLParser()
            parser.feed(text)
            parsed_title = _clean_text(parser.title, 220) or result.title
            body_text = parser.extracted_text(1200)
        elif "text" in content_type or not content_type:
            body_text = _clean_text(text, 1200)
        else:
            return {
                "title": result.title,
                "text": "",
                "used_snippet_only": True,
                "blocked_reason": "unsupported_content_type",
                "content_type": content_type,
            }, "unsupported_content_type"
        return {
            "title": parsed_title,
            "text": body_text,
            "used_snippet_only": False,
            "content_type": content_type,
            "truncated": truncated,
            "robots_txt": _robots_txt_url(result.url),
        }, None

    def _field_level_provenance(self, artifact: OntologyArtifact, source_url: str, summary: str) -> list[dict[str, Any]]:
        payload = _json_load(artifact.payload_json, {})
        fields: list[str] = []
        for field in (
            "name",
            "description",
            "source_object_name",
            "target_object_name",
            "link_type",
            "action_type",
            "source_name",
        ):
            if field in payload:
                fields.append(field)
        if artifact.description:
            fields.append("description")
        if not fields:
            fields.append("payload")
        return [
            {
                "artifact_field": field,
                "source_url": source_url,
                "evidence_summary": summary,
                "proposed_operation": "enrich_context",
                "review_required": True,
            }
            for field in list(dict.fromkeys(fields))
        ]

    def _build_proposal(
        self,
        artifact: OntologyArtifact,
        result: SearchResult,
        crawl: dict[str, Any],
        blocked_reason: str | None,
    ) -> dict[str, Any]:
        page_text = crawl.get("text") or ""
        snippet = result.snippet or ""
        title = crawl.get("title") or result.title
        source_summary = _clean_text(page_text or snippet or title, 900)
        confidence = 0.68
        if blocked_reason:
            confidence = 0.55
        elif page_text:
            confidence = 0.72
        field_provenance = self._field_level_provenance(artifact, result.url, source_summary)
        license_text = ""
        combined_text = " ".join([title, snippet, source_summary]).lower()
        if "cc-by" in combined_text or "creative commons attribution" in combined_text:
            license_text = "CC-BY mentioned by source/search result"
        elif "license" in combined_text:
            license_text = "license mentioned; reviewer must verify terms"
        else:
            license_text = "not detected; reviewer must verify reuse terms"
        robots_risk = "not_checked"
        if crawl.get("robots_txt"):
            robots_risk = f"robots.txt available at {crawl['robots_txt']}; not interpreted by this MVP"
        elif blocked_reason:
            robots_risk = f"crawl skipped: {blocked_reason}"
        digest = _text_digest(
            {
                "target": artifact.canonical_key,
                "url": result.url,
                "summary": source_summary,
                "title": title,
                "field_provenance": field_provenance,
            }
        )
        short_target = artifact.canonical_key.replace(":", "_")[:80]
        canonical_key = canonical_key_for("webenrichment", f"{short_target}:{digest[:16]}")
        return {
            "canonical_key": canonical_key,
            "target_artifact_key": artifact.canonical_key,
            "target_artifact_type": artifact.artifact_type,
            "target_name": artifact.name,
            "source_url": result.url,
            "source_title": title,
            "source_summary": source_summary,
            "confidence": confidence,
            "content_hash": digest,
            "payload": {
                "proposal_type": "external_web_enrichment",
                "target_artifact_key": artifact.canonical_key,
                "target_artifact_type": artifact.artifact_type,
                "target_name": artifact.name,
                "proposed_enrichment": source_summary,
                "source": {
                    "url": result.url,
                    "title": title,
                    "snippet": snippet,
                    "retrieved_at": datetime.utcnow().isoformat() + "Z",
                    "search_query": result.query,
                    "search_rank": result.rank,
                    "search_provider": result.provider,
                    "crawl_status": "snippet_only" if blocked_reason else "crawled",
                    "blocked_reason": blocked_reason,
                    "robots_risk": robots_risk,
                    "license_risk": license_text,
                },
                "field_provenance": field_provenance,
                "governance": {
                    "status": "draft",
                    "review_gate": "ontology_review_required",
                    "canonical_writes": "disabled",
                    "graph_writes": "disabled",
                    "target_artifact_modified": False,
                },
            },
        }

    def run(self, artifact_keys: list[str] | None = None, statuses: set[str] | None = None) -> dict[str, Any]:
        statuses = statuses or {"draft", "needs_changes", "approved"}
        session = self.Session()
        run_key = f"web-enrichment:{self.tenant}:{datetime.utcnow().strftime('%Y%m%d%H%M%S')}:{os.getpid()}"
        run = WebEnrichmentRun(
            project_id=self.tenant,
            run_key=run_key,
            search_provider=self._search_provider_name(),
            status="running",
            safety_profile_json=_json_dump(
                {
                    "allowed_domains": sorted(self.allowed_domains),
                    "allow_discovered_domains": self.allow_discovered_domains,
                    "blocked_private_networks": True,
                    "canonical_writes": "disabled",
                    "graph_writes": "disabled",
                    "write_scope": "draft_enrichment_proposals_only",
                }
            ),
            budget_json=_json_dump(
                {
                    "max_artifacts": self.max_artifacts,
                    "max_results_per_query": self.max_results_per_query,
                    "max_crawl_pages": self.max_crawl_pages,
                    "max_page_bytes": self.max_page_bytes,
                    "timeout_seconds": self.timeout_seconds,
                }
            ),
        )
        session.add(run)
        session.flush()
        try:
            targets = self._fetch_target_artifacts(session, artifact_keys, statuses)
            run.target_artifacts_json = _json_dump([a.canonical_key for a in targets])
            proposal_count = 0
            query_count = 0
            result_count = 0
            crawled = 0
            skipped_sources: list[dict[str, Any]] = []
            for artifact in targets:
                query = self._query_for_artifact(artifact)
                query_count += 1
                results = self.provider.search(query, self.max_results_per_query)
                result_count += len(results)
                seen_urls: set[str] = set()
                for result in results:
                    if not result.url:
                        skipped_sources.append(
                            {
                                "target_artifact_key": artifact.canonical_key,
                                "search_query": query,
                                "reason": "missing_url",
                                "source_title": result.title,
                            }
                        )
                        continue
                    if result.url in seen_urls:
                        skipped_sources.append(
                            {
                                "target_artifact_key": artifact.canonical_key,
                                "search_query": query,
                                "url": result.url,
                                "reason": "duplicate_url",
                            }
                        )
                        continue
                    if not _is_public_web_url(result.url):
                        skipped_sources.append(
                            {
                                "target_artifact_key": artifact.canonical_key,
                                "search_query": query,
                                "url": result.url,
                                "reason": "blocked_non_public_or_sensitive_url",
                            }
                        )
                        continue
                    source_allowed, source_blocked_reason = _is_crawl_allowed(
                        result.url,
                        self.allowed_domains,
                        self.allow_discovered_domains,
                    )
                    if not source_allowed:
                        skipped_sources.append(
                            {
                                "target_artifact_key": artifact.canonical_key,
                                "search_query": query,
                                "url": result.url,
                                "reason": source_blocked_reason,
                            }
                        )
                        continue
                    seen_urls.add(result.url)
                    if crawled < self.max_crawl_pages:
                        try:
                            crawl, blocked_reason = self._crawl_result(result)
                            if not blocked_reason:
                                crawled += 1
                        except Exception as exc:
                            crawl = {
                                "title": result.title,
                                "text": "",
                                "used_snippet_only": True,
                                "blocked_reason": f"crawl_error:{type(exc).__name__}",
                            }
                            blocked_reason = crawl["blocked_reason"]
                    else:
                        crawl = {
                            "title": result.title,
                            "text": "",
                            "used_snippet_only": True,
                            "blocked_reason": "crawl_budget_exhausted",
                        }
                        blocked_reason = "crawl_budget_exhausted"
                    proposal = self._build_proposal(artifact, result, crawl, blocked_reason)
                    natural_key = proposal["canonical_key"].split(":", 1)[1]
                    proposal_artifact = upsert_artifact(
                        session,
                        artifact_type="WebEnrichment",
                        natural_key=natural_key,
                        name=f"Web enrichment for {artifact.name}",
                        description=(
                            f"External web source proposes additional context for {artifact.name}. "
                            "Review is required before any ontology update."
                        ),
                        payload=proposal["payload"],
                        source_refs=[f"artifact:{artifact.canonical_key}", f"web:{proposal['source_url']}"],
                        source_agent="WebEnrichmentAgent",
                        project_id=self.tenant,
                        confidence=proposal["confidence"],
                        status="draft",
                    )
                    replace_evidence(
                        session,
                        proposal_artifact,
                        [
                            {
                                "evidence_type": "target_artifact",
                                "source_ref": f"artifact:{artifact.canonical_key}",
                                "summary": f"Current ontology artifact selected for enrichment: {artifact.name}",
                                "payload": {
                                    "canonical_key": artifact.canonical_key,
                                    "artifact_type": artifact.artifact_type,
                                    "status": artifact.status,
                                    "version": artifact.version,
                                },
                                "confidence": 1.0,
                            },
                            {
                                "evidence_type": "web_source",
                                "source_ref": proposal["source_url"],
                                "summary": proposal["source_summary"],
                                "payload": proposal["payload"]["source"],
                                "content_hash": proposal["content_hash"],
                                "confidence": proposal["confidence"],
                            },
                        ],
                    )
                    existing = (
                        session.query(WebEnrichmentProposal)
                        .filter_by(project_id=self.tenant, proposal_key=proposal_artifact.canonical_key)
                        .first()
                    )
                    if not existing:
                        existing = WebEnrichmentProposal(
                            run_id=run.id,
                            project_id=self.tenant,
                            proposal_key=proposal_artifact.canonical_key,
                        )
                        session.add(existing)
                    existing.run_id = run.id
                    existing.target_artifact_key = artifact.canonical_key
                    existing.ontology_artifact_id = proposal_artifact.id
                    existing.source_url = proposal["source_url"]
                    existing.source_title = proposal["source_title"]
                    existing.summary = proposal["source_summary"]
                    existing.raw_payload_json = _json_dump(proposal["payload"])
                    existing.content_hash = proposal["content_hash"]
                    existing.confidence = proposal["confidence"]
                    existing.status = "draft"
                    proposal_count += 1
            run.query_count = query_count
            run.result_count = result_count
            run.proposal_count = proposal_count
            run.skipped_sources_json = _json_dump(skipped_sources)
            run.status = "completed"
            run.finished_at = datetime.utcnow()
            session.commit()
            return {
                "run_key": run_key,
                "tenant": self.tenant,
                "status": "completed",
                "target_artifacts": [a.canonical_key for a in targets],
                "query_count": query_count,
                "result_count": result_count,
                "proposal_count": proposal_count,
                "skipped_sources": skipped_sources,
                "safety_profile": _json_load(run.safety_profile_json, {}),
            }
        except Exception as exc:
            session.rollback()
            try:
                run.status = "failed"
                run.error = str(exc)
                run.finished_at = datetime.utcnow()
                session.add(run)
                session.commit()
            except Exception:
                session.rollback()
            raise
        finally:
            session.close()


def _parse_csv(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip() for part in value.split(",") if part.strip()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aletheia Web Enrichment Agent")
    parser.add_argument(
        "--target",
        default=os.environ.get(
            "ALETHEIA_PG_URL",
            f"postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/{os.environ.get('ALETHEIA_PG_DB', 'aletheia_ontology')}",
        ),
        help="Metadata/PostGIS connection string.",
    )
    parser.add_argument("--tenant", default=os.environ.get("ALETHEIA_TENANT", "default"))
    parser.add_argument("--artifact", action="append", default=[], help="Target artifact canonical key. Repeatable.")
    parser.add_argument("--artifact-status", default="draft,needs_changes,approved")
    parser.add_argument("--search-results-json", help="Deterministic search result fixture for offline/test runs.")
    parser.add_argument("--seed-url", action="append", default=[], help="Seed URL used as a search result. Repeatable.")
    parser.add_argument("--enable-live-search", action="store_true", help="Use DuckDuckGo HTML search.")
    parser.add_argument("--allowed-domain", action="append", default=[], help="Domain allowlist for page crawling.")
    parser.add_argument(
        "--allow-discovered-domains",
        action="store_true",
        help="Allow crawling public result domains not listed by --allowed-domain.",
    )
    parser.add_argument("--max-artifacts", type=int, default=5)
    parser.add_argument("--max-results-per-query", type=int, default=3)
    parser.add_argument("--max-crawl-pages", type=int, default=2)
    parser.add_argument("--max-page-bytes", type=int, default=256_000)
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--json", action="store_true", help="Print machine-readable result JSON.")
    args = parser.parse_args(argv)

    agent = WebEnrichmentAgent(
        metadata_db_url=args.target,
        tenant=args.tenant,
        search_results_json=args.search_results_json,
        seed_urls=args.seed_url,
        enable_live_search=args.enable_live_search,
        allowed_domains=args.allowed_domain,
        allow_discovered_domains=args.allow_discovered_domains,
        timeout_seconds=args.timeout_seconds,
        max_artifacts=args.max_artifacts,
        max_results_per_query=args.max_results_per_query,
        max_crawl_pages=args.max_crawl_pages,
        max_page_bytes=args.max_page_bytes,
    )
    result = agent.run(artifact_keys=args.artifact, statuses=_parse_csv(args.artifact_status))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            f"web_enrichment status={result['status']} tenant={result['tenant']} "
            f"proposals={result['proposal_count']} run={result['run_key']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
