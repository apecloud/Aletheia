import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "agents"))

from agents.web_enrichment_agent import SearchResult, _is_public_web_url
from iterative_graph_enrichment_agent import GPTResearcherSearchProvider, _is_accepted_research_result_url


class FakeSourceLessResearcher:
    def __init__(self, **kwargs):
        self.query = kwargs["query"]

    async def conduct_research(self):
        return {"raw_result": "Source: None\nTitle: None\nContent: Suez Canal risk evidence."}

    async def write_report(self, report_type="research_report"):
        return "# Report\nSuez Canal risk evidence without preserved source URLs."


class WebEnrichmentUtilitiesTest(unittest.TestCase):
    def test_private_and_sensitive_urls_are_blocked(self):
        self.assertFalse(_is_public_web_url("http://127.0.0.1:8772/private"))
        self.assertFalse(_is_public_web_url("http://localhost:8772/private"))
        self.assertFalse(_is_public_web_url("https://example.org/path?token=secret"))
        self.assertTrue(_is_public_web_url("https://example.org/research"))

    def test_search_result_defaults_to_gpt_researcher_provider(self):
        result = SearchResult(query="q", title="t", url="https://example.org")
        self.assertEqual(result.provider, "gpt_researcher")

    def test_gpt_researcher_report_without_urls_is_accepted(self):
        provider = GPTResearcherSearchProvider(researcher_cls=FakeSourceLessResearcher)
        results = provider.search("Egypt maritime chokepoints", 2)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].provider, "gpt_researcher")
        self.assertTrue(results[0].url.startswith("gpt_researcher://report/"))
        self.assertTrue(_is_accepted_research_result_url(results[0]))


if __name__ == "__main__":
    unittest.main()
