import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "agents"))

from agents.web_enrichment_agent import (
    StaticSearchProvider,
    _is_crawl_allowed,
    _is_public_web_url,
)


class WebEnrichmentSafetyTest(unittest.TestCase):
    def test_private_and_sensitive_urls_are_blocked(self):
        self.assertFalse(_is_public_web_url("http://127.0.0.1:8772/private"))
        self.assertFalse(_is_public_web_url("http://localhost:8772/private"))
        self.assertFalse(_is_public_web_url("https://example.org/path?token=secret"))
        self.assertTrue(_is_public_web_url("https://zenodo.org/records/13841882"))

    def test_crawl_allows_public_domains_without_allowlist(self):
        allowed, reason = _is_crawl_allowed(
            "https://zenodo.org/records/13841882",
            {"zenodo.org"},
            allow_discovered_domains=False,
        )
        self.assertTrue(allowed)
        self.assertIsNone(reason)

        allowed, reason = _is_crawl_allowed(
            "https://example.org/resource",
            {"zenodo.org"},
            allow_discovered_domains=False,
        )
        self.assertTrue(allowed)
        self.assertIsNone(reason)

        allowed, reason = _is_crawl_allowed(
            "https://example.org/resource",
            set(),
            allow_discovered_domains=False,
        )
        self.assertTrue(allowed)
        self.assertIsNone(reason)

    def test_static_search_provider_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "search.json"
            fixture.write_text(
                json.dumps(
                    [
                        {
                            "title": "Zenodo maritime chokepoint dataset",
                            "url": "https://zenodo.org/records/13841882",
                            "snippet": "CC-BY-4.0 maritime chokepoint risk data",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            provider = StaticSearchProvider(str(fixture))
            results = provider.search("chokepoint risk ontology", max_results=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].provider, "static_json")
        self.assertEqual(results[0].url, "https://zenodo.org/records/13841882")


if __name__ == "__main__":
    unittest.main()
