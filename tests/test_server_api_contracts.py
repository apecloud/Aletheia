import json
import threading
import unittest
from dataclasses import dataclass
from http import HTTPStatus
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from server.aletheia_server import AletheiaServerHandler, LocalThreadingHTTPServer


@dataclass(frozen=True)
class FakeTenant:
    tenant_id: str = "tenant-a"
    namespace: str = "tenant_a"
    display_name: str = "Tenant A"
    graph_database: str = "tenant_a_graph"

    def public_dict(self):
        return {
            "tenant_id": self.tenant_id,
            "namespace": self.namespace,
            "display_name": self.display_name,
            "graph_database": self.graph_database,
            "status": "active",
        }


class FakeTenantRegistry:
    default_tenant_id = "tenant-a"

    def __init__(self):
        self.tenant = FakeTenant()

    def get(self, tenant_id=None):
        resolved = tenant_id or self.default_tenant_id
        if resolved != self.tenant.tenant_id:
            raise KeyError(resolved)
        return self.tenant

    def list_public(self):
        return [self.tenant.public_dict()]


class FakeReviewRepository:
    def __init__(self):
        self.tenant_registry = FakeTenantRegistry()
        self.last_filters = None

    def tenant(self, tenant_id=None):
        return self.tenant_registry.get(tenant_id)

    def list_artifacts(self, tenant, filters):
        self.last_filters = dict(filters)
        return {
            "tenant": tenant.public_dict(),
            "artifacts": [
                {
                    "canonical_key": "object:customer",
                    "artifact_type": "object",
                    "name": "Customer",
                    "status": "draft",
                }
            ],
            "stats": [{"artifact_type": "object", "status": "draft", "count": 1}],
        }

    def get_artifact(self, tenant, canonical_key):
        if canonical_key != "object:customer":
            return None
        return {
            "tenant": tenant.public_dict(),
            "canonical_key": canonical_key,
            "artifact_type": "object",
            "name": "Customer",
            "payload": {"object_name": "Customer"},
            "source_schema": {"kind": "object"},
            "evidence": [],
            "reviews": [],
            "canonical": {"status": "draft", "tenant_id": tenant.tenant_id},
            "used_by": [],
        }


class FakeReasoningRepository:
    def __init__(self):
        self.last_status_filter = None
        self.last_question_body = None

    def list_tasks(self, tenant, status_filter=None):
        self.last_status_filter = status_filter
        return {
            "tenant": tenant.public_dict(),
            "tasks": [{"canonical_key": "task-a", "status": status_filter or "active"}],
        }

    def get_task(self, tenant, task_key):
        if task_key != "task-a":
            return None
        return {"tenant": tenant.public_dict(), "task": {"canonical_key": task_key}}

    def list_findings_registry(self, tenant, status=None, context=None, limit=50, filters=None):
        return {"tenant": tenant.public_dict(), "findings": [], "status": status, "context": context, "limit": limit}

    def create_question_task(self, tenant, body):
        self.last_question_body = dict(body)
        if not body.get("question"):
            raise ValueError("question is required")
        return {
            "tenant": tenant.public_dict(),
            "task": {
                "canonical_key": "question:test",
                "question": body["question"],
                "status": "active",
            },
        }


class FakeInstanceRepository:
    def default_center(self, tenant, include_draft=False):
        return None

    def full_graph(self, tenant, object_type=None, instance_id=None, limit=200):
        return None

    def neighborhood(self, tenant, object_type, instance_id, depth=1, limit=200):
        return None

    def proposed_graph_elements(self, tenant, **kwargs):
        return {"tenant": tenant.public_dict(), "elements": [], **kwargs}


class FakeAgentGatewayRepository:
    def list_settings(self, tenant):
        return {"tenant": tenant.public_dict(), "runtimes": [], "runs": []}


class ServerApiContractTest(unittest.TestCase):
    def setUp(self):
        self.review_repository = FakeReviewRepository()
        self.reasoning_repository = FakeReasoningRepository()

        class TestHandler(AletheiaServerHandler):
            repository = self.review_repository
            instance_repository = FakeInstanceRepository()
            reasoning_repository = self.reasoning_repository
            agent_gateway_repository = FakeAgentGatewayRepository()

            def log_message(self, fmt, *args):
                return

        self.server = LocalThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _request_json(self, path, *, method="GET", body=None, headers=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        with self.opener.open(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8")) if response.length != 0 else None
            return response.status, dict(response.headers), payload

    def _request_error(self, path, *, method="GET", body=None):
        with self.assertRaises(HTTPError) as ctx:
            self._request_json(path, method=method, body=body)
        payload = json.loads(ctx.exception.read().decode("utf-8"))
        return ctx.exception.code, payload

    def test_tenants_route_returns_public_tenant_contract_without_private_db_urls(self):
        status, _headers, payload = self._request_json("/api/tenants")

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["default_tenant_id"], "tenant-a")
        self.assertEqual(payload["current"]["tenant_id"], "tenant-a")
        self.assertEqual(payload["tenants"][0]["graph_database"], "tenant_a_graph")
        self.assertNotIn("metadata_db_url", payload["current"])
        self.assertNotIn("source_db_url", payload["current"])

    def test_ontology_catalog_maps_ui_query_aliases_to_repository_filters(self):
        query = urlencode({"tenant": "tenant-a", "kind": "object", "q": "Customer"})

        status, _headers, payload = self._request_json(f"/api/ontology/catalog?{query}")

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["artifacts"][0]["canonical_key"], "object:customer")
        self.assertEqual(self.review_repository.last_filters, {"artifact_type": "object", "search": "Customer"})

    def test_artifact_detail_wraps_definition_and_review_contract(self):
        status, _headers, payload = self._request_json("/api/artifacts/object%3Acustomer?tenant=tenant-a")

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["canonical_key"], "object:customer")
        self.assertEqual(payload["payload"]["object_name"], "Customer")
        self.assertEqual(payload["canonical"]["tenant_id"], "tenant-a")

    def test_reasoning_tasks_route_preserves_status_filter(self):
        status, _headers, payload = self._request_json("/api/reasoning/tasks?tenant=tenant-a&status=active")

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["tasks"][0]["status"], "active")
        self.assertEqual(self.reasoning_repository.last_status_filter, "active")

    def test_reasoning_question_post_returns_created_task_and_validates_body(self):
        status, _headers, payload = self._request_json(
            "/api/reasoning/questions?tenant=tenant-a",
            method="POST",
            body={"question": "What graph evidence supports this claim?"},
        )

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["task"]["canonical_key"], "question:test")
        self.assertEqual(self.reasoning_repository.last_question_body["question"], "What graph evidence supports this claim?")

        error_status, error_payload = self._request_error(
            "/api/reasoning/questions?tenant=tenant-a",
            method="POST",
            body={},
        )
        self.assertEqual(error_status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(error_payload["error"], "question is required")

    def test_graph_context_degrades_without_approved_projection_instead_of_falling_back_to_demo(self):
        status, _headers, payload = self._request_json("/api/graph/context?tenant=tenant-a&view=all")

        self.assertEqual(status, HTTPStatus.OK)
        self.assertFalse(payload["approved"])
        self.assertEqual(payload["nodes"], [])
        self.assertEqual(payload["edges"], [])
        self.assertEqual(payload["scope"]["projection_source"], "none")
        self.assertIn("No reviewed SchemaGraphModelingAgent projection", payload["scope"]["reason"])

    def test_unknown_tenant_and_unknown_api_post_return_json_errors(self):
        status, payload = self._request_error("/api/tenants?tenant=missing")

        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(payload["error"], "'missing'")

        status, payload = self._request_error("/api/unknown?tenant=tenant-a", method="POST", body={})
        self.assertEqual(status, HTTPStatus.NOT_FOUND)
        self.assertEqual(payload["error"], "Unknown API endpoint")

    def test_options_returns_cors_preflight_contract(self):
        request = Request(
            f"{self.base_url}/api/tenants",
            method="OPTIONS",
            headers={"Origin": "http://example.test"},
        )

        with self.opener.open(request, timeout=5) as response:
            self.assertEqual(response.status, HTTPStatus.NO_CONTENT)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "http://example.test")
            self.assertIn("GET", response.headers["Access-Control-Allow-Methods"])
            self.assertIn("POST", response.headers["Access-Control-Allow-Methods"])


if __name__ == "__main__":
    unittest.main()
