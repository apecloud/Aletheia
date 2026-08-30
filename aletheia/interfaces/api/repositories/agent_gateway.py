"""AgentGatewayRepository, extracted from server.py. No behavior change."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from sqlalchemy import bindparam, create_engine, text
from aletheia.interfaces.api.helpers import AGENT_GATEWAY_TMP_DIR, _json_dump, _load_json
from aletheia.interfaces.api.repositories.base import _TenantScopedEngineCache


class AgentGatewayRepository(_TenantScopedEngineCache):
    BLOCKED_TOOLS = {
        "approve",
        "approve_finding",
        "ingest",
        "ingest_graph",
        "modify_canonical_artifact",
        "commit",
        "push",
        "deploy",
        "secret_read",
        "direct_db_write",
    }
    REQUIRED_OUTPUT_FIELDS = {"status", "summary", "tool_calls", "draft_artifacts", "files_touched", "policy_violations"}
    RUNTIME_PROFILES = [
        {
            "runtime_id": "generic_cli_builtin",
            "runtime_type": "generic_cli",
            "binary_ref": sys.executable,
            "command_template_id": "builtin_json_report_v1",
            "enabled": True,
        },
        {"runtime_id": "codex_cli_default", "runtime_type": "codex_cli", "binary_ref": "codex", "command_template_id": "codex_cli_json_report_v1", "enabled": True},
        {"runtime_id": "gemini_cli_default", "runtime_type": "gemini_cli", "binary_ref": "gemini", "command_template_id": "gemini_cli_json_report_v1", "enabled": True},
        {"runtime_id": "claude_code_cli_default", "runtime_type": "claude_code_cli", "binary_ref": "claude", "command_template_id": "claude_code_json_report_v1", "enabled": True},
        {"runtime_id": "openclaw_cli_default", "runtime_type": "openclaw_cli", "binary_ref": "openclaw", "command_template_id": "version_probe_only", "enabled": True},
        {"runtime_id": "hermes_cli_default", "runtime_type": "hermes_cli", "binary_ref": "hermes", "command_template_id": "version_probe_only", "enabled": True},
    ]

    def ensure_defaults(self, tenant):
        with self.metadata_engine_for(tenant).begin() as conn:
            for profile in self.RUNTIME_PROFILES:
                conn.execute(
                    text(
                        """
                        INSERT INTO aletheia_agent_runtime_configs
                        (runtime_id, runtime_type, binary_ref, command_template_id, enabled,
                         health_status, health_detail_json, created_at, updated_at)
                        VALUES
                        (:runtime_id, :runtime_type, :binary_ref, :command_template_id, :enabled,
                         'unknown', '{}', NOW(), NOW())
                        ON CONFLICT (runtime_id) DO UPDATE SET
                          runtime_type = EXCLUDED.runtime_type,
                          binary_ref = EXCLUDED.binary_ref,
                          command_template_id = EXCLUDED.command_template_id,
                          enabled = EXCLUDED.enabled,
                          updated_at = NOW()
                        """
                    ),
                    profile,
                )
            conn.execute(
                text(
                    """
                    INSERT INTO aletheia_agent_policies
                    (project_id, policy_id, allowed_paths_json, allowed_tools_json, blocked_tools_json,
                     max_runtime_seconds, max_output_bytes, env_allowlist_json, secret_policy, created_at, updated_at)
                    VALUES
                    (:tenant_id, 'default_cli_policy', :allowed_paths_json, :allowed_tools_json,
                     :blocked_tools_json, 120, 65536, '[]', 'deny', NOW(), NOW())
                    ON CONFLICT (project_id, policy_id) DO UPDATE SET
                      allowed_paths_json = EXCLUDED.allowed_paths_json,
                      allowed_tools_json = EXCLUDED.allowed_tools_json,
                      blocked_tools_json = EXCLUDED.blocked_tools_json,
                      max_runtime_seconds = EXCLUDED.max_runtime_seconds,
                      max_output_bytes = EXCLUDED.max_output_bytes,
                      env_allowlist_json = EXCLUDED.env_allowlist_json,
                      secret_policy = EXCLUDED.secret_policy,
                      updated_at = NOW()
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "allowed_paths_json": _json_dump(["reports", "web/app", "agents", "README.md"]),
                    "allowed_tools_json": _json_dump(["read", "test", "propose_patch", "propose_finding", "write_report"]),
                    "blocked_tools_json": _json_dump(sorted(self.BLOCKED_TOOLS)),
                },
            )

    def list_settings(self, tenant):
        self.ensure_defaults(tenant)
        with self.metadata_engine_for(tenant).connect() as conn:
            runtimes = conn.execute(
                text(
                    """
                    SELECT runtime_id, runtime_type, binary_ref, command_template_id, enabled,
                           health_status, health_detail_json, created_at, updated_at
                    FROM aletheia_agent_runtime_configs
                    ORDER BY runtime_type, runtime_id
                    """
                )
            ).mappings().all()
            policies = conn.execute(
                text(
                    """
                    SELECT policy_id, project_id, allowed_paths_json, allowed_tools_json,
                           blocked_tools_json, max_runtime_seconds, max_output_bytes,
                           env_allowlist_json, secret_policy, created_at, updated_at
                    FROM aletheia_agent_policies
                    WHERE project_id = :tenant_id
                    ORDER BY policy_id
                    """
                ),
                {"tenant_id": tenant.tenant_id},
            ).mappings().all()
            runs = conn.execute(
                text(
                    """
                    SELECT run_key, project_id, runtime_id, policy_id, task_type, prompt_hash,
                           status, tool_calls_json, policy_violations_json, files_touched_json,
                           output_refs_json, stdout_ref, stderr_ref, started_at, finished_at
                    FROM aletheia_agent_runs
                    WHERE project_id = :tenant_id
                    ORDER BY started_at DESC, id DESC
                    LIMIT 20
                    """
                ),
                {"tenant_id": tenant.tenant_id},
            ).mappings().all()
        return {
            "tenant": tenant.public_dict(),
            "runtimes": [self._runtime_with_readiness(tenant, row) for row in runtimes],
            "policies": [self._policy_to_dict(row) for row in policies],
            "runs": [self._run_to_dict(row) for row in runs],
            "secret_policy": {"storage": "credential_ref_only", "ui": "masked", "default": "deny"},
        }

    def readiness(self, tenant, runtime_id):
        self.ensure_defaults(tenant)
        runtime = self._get_runtime(tenant, runtime_id)
        if not runtime:
            return None
        readiness = self._readiness_for_runtime(tenant, self._runtime_to_dict(runtime))
        return {"tenant": tenant.public_dict(), "readiness": readiness}

    def health_check(self, tenant, runtime_id):
        self.ensure_defaults(tenant)
        runtime = self._get_runtime(tenant, runtime_id)
        if not runtime:
            return None
        status = "unavailable"
        detail = {
            "binary_ref": self._mask_binary(runtime["binary_ref"]),
            "secret_masked": True,
            "command_template_id": runtime["command_template_id"],
        }
        if runtime["command_template_id"] == "builtin_json_report_v1":
            status = "available"
            detail["version"] = f"python {sys.version.split()[0]}"
        else:
            binary = shutil.which(runtime["binary_ref"])
            detail["resolved"] = bool(binary)
            if binary:
                probe = self._probe_version(binary)
                status = "available" if probe["ok"] else "unavailable"
                detail.update(probe)
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE aletheia_agent_runtime_configs
                    SET health_status = :status, health_detail_json = :detail, updated_at = NOW()
                    WHERE runtime_id = :runtime_id
                    """
                ),
                {"status": status, "detail": _json_dump(detail), "runtime_id": runtime_id},
            )
        return {"tenant": tenant.public_dict(), "runtime": {**self._runtime_to_dict(runtime), "health_status": status, "health_detail": detail}}

    def run_smoke(self, tenant, runtime_id, payload):
        self.ensure_defaults(tenant)
        runtime = self._get_runtime(tenant, runtime_id)
        if not runtime:
            return None
        policy = self._get_policy(tenant, payload.get("policy_id") or "default_cli_policy")
        if not policy:
            raise ValueError("policy not found")
        prompt = payload.get("prompt") or "Summarize the Aletheia repository structure as a JSON report."
        task_type = payload.get("task_type") or "report"
        run_key = f"agent-run:{runtime_id}:{int(time.time() * 1000)}"
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        started = time.monotonic()

        command_violation = self._validate_execution(runtime, policy)
        if command_violation:
            output = {
                "status": "blocked",
                "summary": "Command execution blocked by local policy.",
                "tool_calls": [],
                "draft_artifacts": [],
                "files_touched": [],
                "policy_violations": [command_violation],
            }
            stdout, stderr, returncode = _json_dump(output), "", 0
        elif "mock_cli_output" in payload:
            stdout = str(payload.get("mock_cli_output") or "")
            stderr, returncode = "", 0
        else:
            stdout, stderr, returncode = self._execute_runtime(runtime, policy, tenant, prompt, task_type)
            if len(stdout.encode("utf-8")) > policy["max_output_bytes"]:
                stdout = stdout.encode("utf-8")[: policy["max_output_bytes"]].decode("utf-8", errors="ignore")
                stderr = f"{stderr}\noutput truncated by max_output_bytes".strip()

        output, parse_violations = self._parse_cli_output(stdout)
        reported_violations = output.get("policy_violations", [])
        if not isinstance(reported_violations, list):
            reported_violations = [{"code": "invalid_reported_policy_violations"}]
        policy_violations = parse_violations + reported_violations + self._validate_output(output, policy, tenant)
        structural_failure = any(
            violation.get("code") in {"non_json_output", "missing_required_fields"}
            for violation in policy_violations
        )
        status = output.get("status", "failed") if not policy_violations else "blocked"
        if structural_failure:
            status = "failed"
        if returncode != 0:
            status = "failed"
            policy_violations.append({"code": "command_failed", "detail": f"exit code {returncode}"})
        run = self._record_run(
            tenant,
            run_key=run_key,
            runtime_id=runtime["runtime_id"],
            policy_id=policy["policy_id"],
            task_type=task_type,
            prompt_hash=prompt_hash,
            status=status,
            output=output,
            policy_violations=policy_violations,
            stdout=stdout,
            stderr=stderr,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return {"tenant": tenant.public_dict(), "run": run}

    def run_safe_demo(self, tenant, runtime_id, payload):
        readiness_result = self.readiness(tenant, runtime_id)
        if readiness_result is None:
            return None
        readiness = readiness_result["readiness"]
        if readiness["demo_status"] != "demo_ready":
            raise ValueError(f"Safe demo disabled: {readiness['demo_status']}")
        body = dict(payload)
        body.pop("mock_cli_output", None)
        body.setdefault("policy_id", "default_cli_policy")
        body.setdefault("task_type", "report")
        body.setdefault("prompt", "Read the Aletheia README and produce a repository structure smoke report.")
        return self.run_smoke(tenant, runtime_id, body)

    def _execute_runtime(self, runtime, policy, tenant, prompt, task_type):
        if runtime["command_template_id"] in {"claude_code_json_report_v1", "codex_cli_json_report_v1", "gemini_cli_json_report_v1"}:
            return self._execute_external_report_runtime(runtime, policy, tenant, prompt, task_type)
        if runtime["command_template_id"] != "builtin_json_report_v1":
            output = {
                "status": "blocked",
                "summary": f"{runtime['runtime_type']} execution is not enabled in MVP; health check only.",
                "tool_calls": [],
                "draft_artifacts": [],
                "files_touched": [],
                "policy_violations": [{"code": "runtime_probe_only", "runtime_id": runtime["runtime_id"]}],
            }
            return _json_dump(output), "", 0
        script = """
import json
import os
payload = json.loads(os.environ["ALETHEIA_AGENT_TASK"])
print(json.dumps({
  "status": "completed",
  "summary": "Aletheia contains agents, a review workbench, reasoning UI, reports, and evaluation fixtures.",
  "tool_calls": [{"tool": "read", "path": "README.md"}],
  "draft_artifacts": [{
    "artifact_type": "report",
    "payload": {
      "title": "Repository structure smoke report",
      "tenant_id": payload["tenant_id"],
      "task_type": payload["task_type"],
      "summary": "Smoke run produced a draft report only."
    }
  }],
  "files_touched": ["reports/agent-gateway-smoke.md"],
  "policy_violations": [],
  "stdout_ref": "inline",
  "stderr_ref": "inline"
}, sort_keys=True))
"""
        env = {
            "PATH": os.environ.get("PATH", ""),
            "ALETHEIA_AGENT_TASK": _json_dump(
                {
                    "tenant_id": tenant.tenant_id,
                    "task_type": task_type,
                    "prompt": prompt,
                    "allowed_paths": policy["allowed_paths"],
                    "allowed_tools": policy["allowed_tools"],
                    "blocked_tools": policy["blocked_tools"],
                }
            ),
        }
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent,
            env=env,
            text=True,
            capture_output=True,
            timeout=policy["max_runtime_seconds"],
            check=False,
        )
        return result.stdout, result.stderr, result.returncode

    def _execute_external_report_runtime(self, runtime, policy, tenant, prompt, task_type):
        binary = shutil.which(runtime["binary_ref"])
        if not binary:
            output = {
                "status": "blocked",
                "summary": f"{runtime['binary_ref']} is not visible to the service PATH.",
                "tool_calls": [],
                "draft_artifacts": [],
                "files_touched": [],
                "policy_violations": [{"code": "runtime_binary_missing", "runtime_id": runtime["runtime_id"]}],
            }
            return _json_dump(output), "", 0
        safe_prompt = self._safe_demo_prompt(runtime, tenant, task_type, prompt)
        last_message_path = AGENT_GATEWAY_TMP_DIR / f"aletheia-{runtime['runtime_id']}-{int(time.time() * 1000)}.txt"
        command = self._runtime_command(runtime, binary, safe_prompt, last_message_path)
        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                cwd=Path(__file__).resolve().parent,
                env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
                text=True,
                capture_output=True,
                timeout=policy["max_runtime_seconds"],
                check=False,
            )
            raw_output = self._extract_runtime_response(runtime, result.stdout, result.stderr, last_message_path)
            policy_violations = []
            status = "completed" if result.returncode == 0 and raw_output else "failed"
            if result.returncode != 0:
                policy_violations.append({"code": "runtime_command_failed", "detail": f"exit code {result.returncode}"})
        except subprocess.TimeoutExpired:
            raw_output = ""
            result = subprocess.CompletedProcess(command, 124, "", "safe demo timed out")
            status = "failed"
            policy_violations = [{"code": "runtime_timeout", "detail": f">{policy['max_runtime_seconds']}s"}]
        finally:
            try:
                last_message_path.unlink()
            except FileNotFoundError:
                pass
        raw_summary = raw_output.strip().replace("\n", " ")[:160] or "no response"
        summary = f"{runtime['runtime_type']} safe demo completed with read-only structured report output: {raw_summary}"
        output = {
            "status": status,
            "summary": summary,
            "tool_calls": [{"tool": "write_report", "runtime": runtime["runtime_id"], "mode": "safe_demo"}],
            "draft_artifacts": [
                {
                    "artifact_type": "report",
                    "status": "draft",
                    "payload": {
                        "title": f"{runtime['runtime_type']} safe demo report",
                        "tenant_id": tenant.tenant_id,
                        "task_type": task_type,
                        "summary": summary,
                        "runtime_id": runtime["runtime_id"],
                        "command_template_id": runtime["command_template_id"],
                        "raw_response": self._mask_secret_like(raw_output)[:4000],
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                }
            ],
            "files_touched": ["reports/agent-gateway-smoke.md"],
            "policy_violations": policy_violations,
        }
        runtime_log = _json_dump({"runtime_stdout": self._mask_secret_like(result.stdout), "runtime_stderr": self._mask_secret_like(result.stderr)})
        return _json_dump(output), runtime_log, result.returncode

    def _safe_demo_prompt(self, runtime, tenant, task_type, prompt):
        return "Say OK."

    def _runtime_command(self, runtime, binary, safe_prompt, last_message_path):
        template = runtime["command_template_id"]
        if template == "claude_code_json_report_v1":
            return [binary, "--print", "--output-format", "json", "--permission-mode", "plan", safe_prompt]
        if template == "codex_cli_json_report_v1":
            return [binary, "exec", "--cd", str(Path(__file__).resolve().parent), "--sandbox", "read-only", "--output-last-message", str(last_message_path), safe_prompt]
        if template == "gemini_cli_json_report_v1":
            return [binary, "--prompt", safe_prompt, "--approval-mode", "plan", "--output-format", "json"]
        raise ValueError(f"Unsupported runtime template: {template}")

    def _extract_runtime_response(self, runtime, stdout, stderr, last_message_path):
        template = runtime["command_template_id"]
        if template == "claude_code_json_report_v1":
            try:
                return str(json.loads(stdout or "{}").get("result") or "")
            except json.JSONDecodeError:
                return stdout or stderr
        if template == "codex_cli_json_report_v1":
            if last_message_path.exists():
                return last_message_path.read_text(encoding="utf-8", errors="ignore")
            return stdout or stderr
        if template == "gemini_cli_json_report_v1":
            try:
                return str(json.loads(stdout or "{}").get("response") or "")
            except json.JSONDecodeError:
                return stdout or stderr
        return stdout or stderr

    def _validate_execution(self, runtime, policy):
        if runtime["command_template_id"] not in {
            "builtin_json_report_v1",
            "version_probe_only",
            "claude_code_json_report_v1",
            "codex_cli_json_report_v1",
            "gemini_cli_json_report_v1",
        }:
            return {"code": "command_template_not_allowlisted", "detail": runtime["command_template_id"]}
        if policy["secret_policy"] != "deny":
            return {"code": "secret_policy_not_supported_in_mvp", "detail": policy["secret_policy"]}
        return None

    def _runtime_with_readiness(self, tenant, row):
        runtime = self._runtime_to_dict(row)
        runtime["readiness"] = self._readiness_for_runtime(tenant, runtime)
        return runtime

    def _readiness_for_runtime(self, tenant, runtime):
        policy = self._get_policy(tenant, "default_cli_policy")
        checks = []

        if not runtime["enabled"]:
            checks.append(self._check("runtime_enabled", "blocked", "Runtime profile is disabled.", "Enable the runtime profile before demo."))
        else:
            checks.append(self._check("runtime_enabled", "pass", "Runtime profile is enabled."))

        if runtime["command_template_id"] == "builtin_json_report_v1":
            binary_status = "pass"
            binary_detail = f"{runtime['binary_ref']} available for builtin template."
        else:
            binary_found = bool(shutil.which(runtime["binary_ref"]))
            binary_status = "pass" if binary_found else "fail"
            binary_detail = f"{runtime['binary_ref']} found in service PATH." if binary_found else f"{runtime['binary_ref']} is not visible to the service PATH."
        checks.append(
            self._check(
                "binary",
                binary_status,
                binary_detail,
                f"Install {runtime['binary_ref']} and restart the service with a PATH that can resolve it." if binary_status == "fail" else "",
            )
        )

        path_status = "pass" if runtime["command_template_id"] == "builtin_json_report_v1" or binary_status == "pass" else "fail"
        checks.append(
            self._check(
                "path_visible",
                path_status,
                "Runtime binary is visible to the service process." if path_status == "pass" else "Runtime binary is not visible from the background service.",
                f"Expose {runtime['binary_ref']} through the service PATH, not only the interactive shell." if path_status == "fail" else "",
            )
        )

        if runtime["runtime_type"] == "generic_cli":
            checks.append(self._check("auth", "pass", "No external auth required."))
        elif binary_status == "fail":
            checks.append(self._check("auth", "unknown", "Auth was not checked because the binary is unavailable.", f"Install and sign in to {runtime['binary_ref']} locally; credentials are not stored in Aletheia."))
        else:
            auth_check = self._auth_check(runtime)
            checks.append(auth_check)

        expected_template = self._expected_demo_template(runtime["runtime_type"])
        template_ready = runtime["command_template_id"] == expected_template
        checks.append(
            self._check(
                "template",
                "pass" if template_ready else "fail",
                f"{runtime['command_template_id']} is configured." if template_ready else f"{expected_template} is required for executable demo; current template is {runtime['command_template_id']}.",
                f"Add an allowlisted {expected_template} runtime template before enabling safe demo." if not template_ready else "",
            )
        )

        output_ready = runtime["command_template_id"] in {
            "builtin_json_report_v1",
            "claude_code_json_report_v1",
            "codex_cli_json_report_v1",
            "gemini_cli_json_report_v1",
        }
        checks.append(
            self._check(
                "output_contract",
                "pass" if output_ready else "fail",
                "Structured report output is supported by the gateway adapter." if output_ready else "No structured output parser is enabled for this placeholder profile.",
                "Configure a JSON/report adapter that maps CLI output into draft artifacts only." if not output_ready else "",
            )
        )

        if policy:
            policy_ok = (
                policy["secret_policy"] == "deny"
                and "reports" in policy["allowed_paths"]
                and set(self.BLOCKED_TOOLS).issubset(set(policy["blocked_tools"]))
            )
            checks.append(
                self._check(
                    "policy",
                    "pass" if policy_ok else "fail",
                    "Default CLI policy is deny-by-default with allowed paths and blocked actions." if policy_ok else "Default CLI policy is missing required safe-demo boundaries.",
                    "Restore default_cli_policy with secret_policy=deny, reports allowed path, and blocked tools." if not policy_ok else "",
                )
            )
        else:
            checks.append(self._check("policy", "fail", "default_cli_policy not found.", "Create the default CLI policy before running demos."))

        checks.append(self._check("working_dir", "pass", str(Path(__file__).resolve().parent)))
        checks.append(
            self._check(
                "smoke_task",
                "pass" if output_ready and template_ready else "fail",
                "Read-only repository summary safe demo." if output_ready and template_ready else "Safe demo is blocked until an executable template exists.",
                "Use generic_cli_builtin now, or add a controlled template for this CLI." if not (output_ready and template_ready) else "",
            )
        )

        if any(check["status"] == "blocked" for check in checks):
            demo_status = "disabled_by_policy"
        elif any(check["name"] == "binary" and check["status"] == "fail" for check in checks):
            demo_status = "not_installed"
        elif any(check["name"] == "path_visible" and check["status"] == "fail" for check in checks):
            demo_status = "path_not_visible"
        elif any(check["name"] == "auth" and check["status"] == "fail" for check in checks):
            demo_status = "auth_missing"
        elif any(check["name"] in {"template", "output_contract"} and check["status"] == "fail" for check in checks):
            demo_status = "output_contract_missing"
        elif any(check["name"] == "policy" and check["status"] == "fail" for check in checks):
            demo_status = "policy_not_ready"
        elif all(check["status"] in {"pass"} for check in checks):
            demo_status = "demo_ready"
        else:
            demo_status = "output_contract_missing"

        return {
            "runtime_id": runtime["runtime_id"],
            "demo_status": demo_status,
            "safe_demo_enabled": demo_status == "demo_ready",
            "checks": checks,
        }

    def _check(self, name, status, detail, next_action=""):
        return {
            "name": name,
            "status": status,
            "detail": self._mask_secret_like(detail),
            "next_action": self._mask_secret_like(next_action),
        }

    def _expected_demo_template(self, runtime_type):
        return {
            "claude_code_cli": "claude_code_json_report_v1",
            "codex_cli": "codex_cli_json_report_v1",
            "gemini_cli": "gemini_cli_json_report_v1",
            "openclaw_cli": "openclaw_cli_json_report_v1",
            "hermes_cli": "hermes_cli_json_report_v1",
            "generic_cli": "builtin_json_report_v1",
        }.get(runtime_type, f"{runtime_type}_json_report_v1")

    def _auth_check(self, runtime):
        runtime_type = runtime["runtime_type"]
        binary = runtime["binary_ref"]
        if runtime_type == "claude_code_cli":
            return self._run_auth_command("auth", [binary, "auth", "status"], "Claude Code auth is available.", f"Run `{binary} auth` locally and sign in.")
        if runtime_type == "codex_cli":
            return self._run_auth_command("auth", [binary, "login", "status"], "Codex CLI auth is available.", f"Run `{binary} login` locally and sign in.")
        if runtime_type == "gemini_cli":
            return self._run_auth_command("auth", [binary, "--version"], "Gemini CLI binary is available; auth is validated during safe demo execution.", f"Run `{binary}` locally and complete sign-in if safe demo reports auth failure.")
        return self._check("auth", "unknown", "Auth check is not implemented for this runtime.", f"Install and sign in to {binary} locally; credentials are not stored in Aletheia.")

    def _run_auth_command(self, name, command, success_detail, next_action):
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=8, check=False)
        except (OSError, subprocess.SubprocessError):
            return self._check(name, "fail", "Auth command failed or timed out.", next_action)
        output = self._mask_secret_like(result.stdout or result.stderr)
        if result.returncode == 0:
            return self._check(name, "pass", f"{success_detail} {output[:160]}")
        return self._check(name, "fail", f"Auth command failed. {output[:160]}", next_action)

    def _parse_cli_output(self, stdout):
        try:
            output = json.loads(stdout)
        except json.JSONDecodeError as exc:
            return {"status": "failed", "summary": "CLI output was not valid JSON", "tool_calls": [], "draft_artifacts": [], "files_touched": [], "policy_violations": []}, [
                {"code": "non_json_output", "detail": str(exc)}
            ]
        missing = sorted(self.REQUIRED_OUTPUT_FIELDS - set(output))
        if missing:
            return output, [{"code": "missing_required_fields", "fields": missing}]
        return output, []

    def _validate_output(self, output, policy, tenant):
        violations = []
        blocked = set(policy["blocked_tools"]) | self.BLOCKED_TOOLS
        allowed = set(policy["allowed_tools"])
        for call in output.get("tool_calls", []):
            tool = str(call.get("tool") or call.get("name") or "").lower()
            if tool in blocked:
                violations.append({"code": "blocked_tool_call", "tool": tool})
            if tool and tool not in allowed and tool not in blocked:
                violations.append({"code": "tool_not_allowed", "tool": tool})
        text_blob = _json_dump(output).lower()
        for blocked_word in sorted(blocked):
            pattern = r"(?<![a-z0-9_/-])" + re.escape(blocked_word.lower()) + r"(?![a-z0-9_/-])"
            if re.search(pattern, text_blob):
                violations.append({"code": "blocked_action_in_output", "action": blocked_word})
        for path in output.get("files_touched", []):
            if not self._path_allowed(path, policy["allowed_paths"]):
                violations.append({"code": "path_not_allowed", "path": path})
        for artifact in output.get("draft_artifacts", []):
            status = artifact.get("status", "draft")
            if status not in {"draft", "accepted_for_review"}:
                violations.append({"code": "non_draft_output", "status": status})
            payload = artifact.get("payload", {})
            if payload.get("tenant_id") and payload.get("tenant_id") != tenant.tenant_id:
                violations.append({"code": "tenant_mismatch", "tenant_id": payload.get("tenant_id")})
        return violations

    def _path_allowed(self, path, allowed_paths):
        normalized = str(path).strip().lstrip("/")
        if ".." in Path(normalized).parts:
            return False
        return any(normalized == allowed.rstrip("/") or normalized.startswith(f"{allowed.rstrip('/')}/") for allowed in allowed_paths)

    def _record_run(self, tenant, *, run_key, runtime_id, policy_id, task_type, prompt_hash, status, output, policy_violations, stdout, stderr, latency_ms):
        files_touched = output.get("files_touched", [])
        tool_calls = output.get("tool_calls", [])
        output_refs = {
            "summary": output.get("summary"),
            "stdout_ref": "inline_masked",
            "stderr_ref": "inline_masked",
            "latency_ms": latency_ms,
        }
        with self.metadata_engine_for(tenant).begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO aletheia_agent_runs
                    (run_key, project_id, runtime_id, policy_id, task_type, prompt_hash,
                     status, tool_calls_json, policy_violations_json, files_touched_json,
                     output_refs_json, stdout_ref, stderr_ref, started_at, finished_at)
                    VALUES
                    (:run_key, :tenant_id, :runtime_id, :policy_id, :task_type, :prompt_hash,
                     :status, :tool_calls_json, :policy_violations_json, :files_touched_json,
                     :output_refs_json, :stdout_ref, :stderr_ref, NOW(), NOW())
                    RETURNING run_key, project_id, runtime_id, policy_id, task_type, prompt_hash,
                              status, tool_calls_json, policy_violations_json, files_touched_json,
                              output_refs_json, stdout_ref, stderr_ref, started_at, finished_at
                    """
                ),
                {
                    "run_key": run_key,
                    "tenant_id": tenant.tenant_id,
                    "runtime_id": runtime_id,
                    "policy_id": policy_id,
                    "task_type": task_type,
                    "prompt_hash": prompt_hash,
                    "status": status,
                    "tool_calls_json": _json_dump(tool_calls),
                    "policy_violations_json": _json_dump(policy_violations),
                    "files_touched_json": _json_dump(files_touched),
                    "output_refs_json": _json_dump(output_refs),
                    "stdout_ref": self._mask_secret_like(stdout),
                    "stderr_ref": self._mask_secret_like(stderr),
                },
            ).mappings().first()
            run_id = conn.execute(text("SELECT id FROM aletheia_agent_runs WHERE project_id = :tenant_id AND run_key = :run_key"), {"tenant_id": tenant.tenant_id, "run_key": run_key}).scalar()
            if status == "completed":
                for artifact in output.get("draft_artifacts", []):
                    conn.execute(
                        text(
                            """
                            INSERT INTO aletheia_agent_output_artifacts
                            (run_id, project_id, artifact_type, payload_json, status, created_at)
                            VALUES (:run_id, :tenant_id, :artifact_type, :payload_json, :status, NOW())
                            """
                        ),
                        {
                            "run_id": run_id,
                            "tenant_id": tenant.tenant_id,
                            "artifact_type": artifact.get("artifact_type", "report"),
                            "payload_json": _json_dump(artifact.get("payload", {})),
                            "status": artifact.get("status", "draft"),
                        },
                    )
        return self._run_to_dict(row)

    def _get_runtime(self, tenant, runtime_id):
        with self.metadata_engine_for(tenant).connect() as conn:
            return conn.execute(
                text(
                    """
                    SELECT runtime_id, runtime_type, binary_ref, command_template_id, enabled,
                           health_status, health_detail_json, created_at, updated_at
                    FROM aletheia_agent_runtime_configs
                    WHERE runtime_id = :runtime_id
                    """
                ),
                {"runtime_id": runtime_id},
            ).mappings().first()

    def _get_policy(self, tenant, policy_id):
        with self.metadata_engine_for(tenant).connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT policy_id, project_id, allowed_paths_json, allowed_tools_json,
                           blocked_tools_json, max_runtime_seconds, max_output_bytes,
                           env_allowlist_json, secret_policy, created_at, updated_at
                    FROM aletheia_agent_policies
                    WHERE project_id = :tenant_id AND policy_id = :policy_id
                    """
                ),
                {"tenant_id": tenant.tenant_id, "policy_id": policy_id},
            ).mappings().first()
        return self._policy_to_dict(row) if row else None

    def _probe_version(self, binary):
        for args in ([binary, "--version"], [binary, "version"]):
            try:
                result = subprocess.run(args, text=True, capture_output=True, timeout=5, check=False)
                output = (result.stdout or result.stderr or "").strip().splitlines()
                if result.returncode == 0 and output:
                    return {"ok": True, "version": self._mask_secret_like(output[0])[:240]}
            except (OSError, subprocess.SubprocessError):
                continue
        return {"ok": False, "version": "unavailable"}

    def _mask_binary(self, value):
        return Path(value).name if "/" in value else value

    def _mask_secret_like(self, value):
        text_value = str(value or "")
        secret_patterns = [
            r"sk-[A-Za-z0-9*_-]{8,}",
            r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^\\s,'\"]+",
        ]
        for pattern in secret_patterns:
            text_value = re.sub(pattern, "[masked]", text_value)
        for key in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"):
            if key in text_value.upper():
                return "[masked]"
        return text_value[:8192]

    def _runtime_to_dict(self, row):
        return {
            "runtime_id": row["runtime_id"],
            "runtime_type": row["runtime_type"],
            "binary_ref": self._mask_binary(row["binary_ref"]),
            "command_template_id": row["command_template_id"],
            "enabled": row["enabled"],
            "health_status": row["health_status"],
            "health_detail": _load_json(row["health_detail_json"], {}),
            "created_at": str(row["created_at"]) if row.get("created_at") else None,
            "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
        }

    def _policy_to_dict(self, row):
        return {
            "policy_id": row["policy_id"],
            "tenant_id": row["project_id"],
            "allowed_paths": _load_json(row["allowed_paths_json"], []),
            "allowed_tools": _load_json(row["allowed_tools_json"], []),
            "blocked_tools": _load_json(row["blocked_tools_json"], []),
            "max_runtime_seconds": row["max_runtime_seconds"],
            "max_output_bytes": row["max_output_bytes"],
            "env_allowlist": _load_json(row["env_allowlist_json"], []),
            "secret_policy": row["secret_policy"],
            "created_at": str(row["created_at"]) if row.get("created_at") else None,
            "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
        }

    def _run_to_dict(self, row):
        return {
            "run_key": row["run_key"],
            "tenant_id": row["project_id"],
            "runtime_id": row["runtime_id"],
            "policy_id": row["policy_id"],
            "task_type": row["task_type"],
            "prompt_hash": row["prompt_hash"],
            "status": row["status"],
            "tool_calls": _load_json(row["tool_calls_json"], []),
            "policy_violations": _load_json(row["policy_violations_json"], []),
            "files_touched": _load_json(row["files_touched_json"], []),
            "output_refs": _load_json(row["output_refs_json"], {}),
            "stdout_ref": row["stdout_ref"],
            "stderr_ref": row["stderr_ref"],
            "started_at": str(row["started_at"]) if row["started_at"] else None,
            "finished_at": str(row["finished_at"]) if row["finished_at"] else None,
        }
