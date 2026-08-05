from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


TOOL = Path(__file__).resolve().parents[1]
CLI = TOOL / "bin/plan-exec-cache"


class RuntimeTests(unittest.TestCase):
    PROCEDURE = """# Procedure

## Description

Inspect the target identified by the request and report verified findings.

## Preconditions and constraints

- The target can be inspected with the configured read-only interface.

## Request binding

- `$TARGET`: bind the target named in the request without substituting it.

## Runtime values

- Resolve the target from the current request.

## Source compliance

- Required: use the configured read-only interface in Step 2.
- Forbidden: do not modify the target.

## Steps

1. Identify the target from the current request.
2. Inspect it:

```bash
inspect-target "$TARGET"
```

## Verification

- Confirm the requested target was inspected.
"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.source = self.repo / "instructions.md"
        self.source.write_text("# Instructions\n")
        self.home = self.root / "state"
        self.env = {
            **os.environ,
            "PLAN_EXECUTE_CACHE": "1",
            "PLAN_EXECUTE_CACHE_HOME": str(self.home),
            "PLAN_EXECUTE_CACHE_REPO_ROOT": str(self.repo),
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def call(self, *args: str, stdin: str | None = None,
             env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(CLI), *args], input=stdin, capture_output=True, text=True,
            env={**self.env, **(env or {})}, timeout=20,
        )

    def remember(self, key: str = "demo.inspect",
                 procedure: str | None = None,
                 source: Path | None = None) -> subprocess.CompletedProcess:
        return self.call(
            "remember", "--key", key,
            "--source", str(source or self.source),
            "--procedure-file", "-", stdin=procedure or self.PROCEDURE,
        )

    def test_recall_miss(self) -> None:
        result = self.call("recall", "--key", "demo.inspect")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stderr.strip(), "MISS")

    def test_remember_and_recall(self) -> None:
        remembered = self.remember()
        self.assertEqual(remembered.returncode, 0, remembered.stderr)
        metadata = json.loads(
            (self.home / "memories/demo.inspect/metadata.json").read_text()
        )
        self.assertNotIn("status", metadata)
        self.assertNotIn("successful_runs", metadata)
        recalled = self.call("recall", "--key", "demo.inspect")
        self.assertEqual(recalled.returncode, 0, recalled.stderr)
        self.assertIn("Identify the target from the current request", recalled.stdout)

    def test_nested_checkpoint_is_part_of_steps(self) -> None:
        procedure = self.PROCEDURE.replace(
            "1. Identify the target from the current request.\n2. Inspect it:",
            "### Checkpoint 1 — Inspect\n\nIdentify and inspect the target:",
        )
        remembered = self.remember(procedure=procedure)
        self.assertEqual(remembered.returncode, 0, remembered.stderr)

    def test_source_change_invalidates_recall(self) -> None:
        self.assertEqual(self.remember().returncode, 0)
        self.source.write_text("# Changed instructions\n")
        inventory = json.loads(self.call("list", "--json").stdout)
        self.assertEqual(inventory, [])
        self.assertEqual(
            self.call("recall", "--key", "demo.inspect").returncode, 3,
        )

    def test_manual_procedure_edit_invalidates_recall(self) -> None:
        self.assertEqual(self.remember().returncode, 0)
        procedure = self.home / "memories/demo.inspect/procedure.md"
        procedure.write_text(procedure.read_text() + "\nmanual edit\n")
        self.assertEqual(
            self.call("recall", "--key", "demo.inspect").returncode, 3,
        )

    def test_directory_source_change_invalidates_recall(self) -> None:
        source_dir = self.repo / "instructions"
        source_dir.mkdir()
        child = source_dir / "procedure.md"
        child.write_text("first procedure\n")
        remembered = self.remember("demo.directory", source=source_dir)
        self.assertEqual(remembered.returncode, 0, remembered.stderr)
        child.write_text("changed procedure\n")
        self.assertEqual(
            self.call("recall", "--key", "demo.directory").returncode, 3,
        )

    def test_missing_source_is_rejected(self) -> None:
        remembered = self.call(
            "remember", "--key", "demo.missing",
            "--source", str(self.repo / "missing.md"),
            "--procedure-file", "-", stdin=self.PROCEDURE,
        )
        self.assertEqual(remembered.returncode, 2)
        self.assertIn("source does not exist", remembered.stderr)

    def test_remember_replaces_a_repaired_procedure(self) -> None:
        self.assertEqual(self.remember().returncode, 0)
        repaired = self.PROCEDURE.replace(
            "2. Inspect it:", "2. Inspect it once:"
        )
        self.assertEqual(self.remember(procedure=repaired).returncode, 0)
        recalled = self.call("recall", "--key", "demo.inspect")
        self.assertEqual(recalled.returncode, 0, recalled.stderr)
        self.assertIn("Inspect it once", recalled.stdout)

    def test_remember_requires_a_source(self) -> None:
        result = self.call(
            "remember", "--key", "demo.inspect", "--procedure-file", "-",
            stdin=self.PROCEDURE,
        )
        self.assertEqual(result.returncode, 2)

    def test_input_placeholders_are_rejected(self) -> None:
        procedure = self.PROCEDURE.replace("the target", "`{{input.file}}`")
        result = self.remember("demo.file", procedure)
        self.assertEqual(result.returncode, 2)
        self.assertIn("resolve request values during execution", result.stderr)

    def test_workspace_path_is_rejected(self) -> None:
        procedure = self.PROCEDURE.replace(
            "Inspect it:", f"Inspect {self.repo}/current-target:"
        )
        result = self.remember("demo.workspace-path", procedure)
        self.assertEqual(result.returncode, 2)
        self.assertIn("current workspace path", result.stderr)

    def test_invalid_bash_is_rejected(self) -> None:
        procedure = self.PROCEDURE.replace(
            "```bash\ninspect-target \"$TARGET\"\n```",
            "```bash\nif then\n```",
        )
        result = self.remember("demo.shell", procedure)
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid", result.stderr)

    def test_description_is_required(self) -> None:
        procedure = re.sub(
            r"(?ms)^## Description\n.*?(?=^## Steps)", "", self.PROCEDURE
        )
        result = self.remember("demo.undescribed", procedure)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Description section", result.stderr)

    def test_constraints_are_required(self) -> None:
        procedure = re.sub(
            r"(?ms)^## Preconditions and constraints\n.*?(?=^## Runtime values)",
            "", self.PROCEDURE,
        )
        result = self.remember("demo.unconstrained", procedure)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Preconditions and constraints section", result.stderr)

    def test_request_binding_is_required(self) -> None:
        procedure = re.sub(
            r"(?ms)^## Request binding\n.*?(?=^## Runtime values)",
            "", self.PROCEDURE,
        )
        result = self.remember("demo.unbound", procedure)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Request binding section", result.stderr)

    def test_source_compliance_is_required(self) -> None:
        procedure = re.sub(
            r"(?ms)^## Source compliance\n.*?(?=^## Steps)",
            "", self.PROCEDURE,
        )
        result = self.remember("demo.unchecked", procedure)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Source compliance section", result.stderr)

    def test_source_compliance_requires_mapping(self) -> None:
        procedure = self.PROCEDURE.replace(
            "- Required: use the configured read-only interface in Step 2.",
            "- The configured interface is used.",
        )
        result = self.remember("demo.unmapped", procedure)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Required: mapping", result.stderr)

    def test_request_binding_requires_variable_or_none(self) -> None:
        procedure = self.PROCEDURE.replace(
            "- `$TARGET`: bind the target named in the request without "
            "substituting it.",
            "- Bind the target named in the request.",
        )
        result = self.remember("demo.implicit-binding", procedure)
        self.assertEqual(result.returncode, 2)
        self.assertIn("request-derived $VARIABLE", result.stderr)

    def test_request_bound_variable_is_not_assigned(self) -> None:
        procedure = self.PROCEDURE.replace(
            'inspect-target "$TARGET"',
            'TARGET="current-target"\ninspect-target "$TARGET"',
        )
        result = self.remember("demo.literal-binding", procedure)
        self.assertEqual(result.returncode, 2)
        self.assertIn("assigns request-bound $TARGET", result.stderr)

    def test_action_variable_must_be_declared(self) -> None:
        procedure = self.PROCEDURE.replace(
            'inspect-target "$TARGET"',
            'inspect-target "$TARGET" --host "$HOST_IP"',
        )
        result = self.remember("demo.undeclared", procedure)
        self.assertEqual(result.returncode, 2)
        self.assertIn("undeclared runtime values: $HOST_IP", result.stderr)

    def test_generic_example_is_allowed(self) -> None:
        procedure = self.PROCEDURE.replace(
            "The target can be inspected",
            "The target can be inspected (for example, through a local API)",
        )
        result = self.remember("demo.example", procedure)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_credential_is_rejected(self) -> None:
        procedure = self.PROCEDURE.replace(
            "Inspect it:", "Use TOKEN=secret-value to inspect it:"
        )
        result = self.remember("demo.secret", procedure)
        self.assertEqual(result.returncode, 2)
        self.assertIn("credential", result.stderr)

    def test_numeric_token_limit_is_not_a_credential(self) -> None:
        procedure = self.PROCEDURE.replace(
            '```bash\ninspect-target "$TARGET"\n```',
            '```bash\nprintf \'{"max_tokens": 1024}\\n\'\n```',
        )
        result = self.remember("demo.token-limit", procedure)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_credential_variable_reference_is_not_a_credential(self) -> None:
        procedure = self.PROCEDURE.replace(
            "- Resolve the target from the current request.",
            "- `$NGC_CLI_API_KEY`: inherited from the host environment.\n"
            "- `$NVIDIA_API_KEY`: inherited from the host environment.",
        ).replace(
            '```bash\ninspect-target "$TARGET"\n```',
            "```bash\n"
            "NGC_KEY=\"${NGC_CLI_API_KEY}\"\n"
            "NVIDIA_KEY=\"${NVIDIA_API_KEY:-}\"\n```",
        )
        result = self.remember("demo.credential-reference", procedure)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_credential_literal_default_is_rejected(self) -> None:
        procedure = self.PROCEDURE.replace(
            '```bash\ninspect-target "$TARGET"\n```',
            "```bash\nTOKEN=\"${TOKEN:-secret-value}\"\n```",
        )
        result = self.remember("demo.credential-default", procedure)
        self.assertEqual(result.returncode, 2)
        self.assertIn("credential", result.stderr)

    def test_action_block_is_required(self) -> None:
        procedure = re.sub(
            r"(?ms)^```bash\n.*?^```\s*", "Inspect the target.\n",
            self.PROCEDURE,
        )
        result = self.remember("demo.no-action", procedure)
        self.assertEqual(result.returncode, 2)
        self.assertIn("complete bash, sh, or tool block", result.stderr)

    def test_structured_tool_block_is_supported(self) -> None:
        procedure = self.PROCEDURE.replace(
            '```bash\ninspect-target "$TARGET"\n```',
            '```tool\n{"name":"inspect","input":{"target":"$TARGET"}}\n```',
        )
        result = self.remember("demo.tool", procedure)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_forget_removes_memory(self) -> None:
        self.assertEqual(self.remember().returncode, 0)
        self.assertEqual(
            self.call("forget", "--key", "demo.inspect").returncode, 0,
        )
        self.assertEqual(
            self.call("recall", "--key", "demo.inspect").returncode, 3,
        )

    def test_plan_execute_cache_is_the_only_mode_switch(self) -> None:
        result = self.call(
            "recall", "--key", "demo.inspect", env={"PLAN_EXECUTE_CACHE": "0"}
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("PLAN_EXECUTE_CACHE=1", result.stderr)


if __name__ == "__main__":
    unittest.main()
