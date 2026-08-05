from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


TOOL = Path(__file__).resolve().parents[1]
HOOK = TOOL / "adapters/claude_code/adapter.py"
CLI = TOOL / "bin/plan-exec-cache"
SETTINGS = TOOL.parents[1] / ".claude/settings.json"


class AdapterTests(unittest.TestCase):
    def call(self, home: str, *, enabled: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["python3", str(HOOK)], capture_output=True, text=True, check=True,
            env={
                **os.environ,
                "PLAN_EXECUTE_CACHE": "1" if enabled else "0",
                "PLAN_EXECUTE_CACHE_HOME": home,
                "PLAN_EXECUTE_CACHE_REPO_ROOT": str(TOOL.parents[1]),
            },
        )

    def test_disabled_session_receives_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(self.call(home, enabled=False).stdout, "")

    def test_session_receives_inventory_and_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            source = Path(home) / "instructions.md"
            source.write_text("# Inspect instructions\n")
            procedure = """# Procedure

## Description
Inspect a requested target.
## Preconditions and constraints
- A read-only inspection interface is available.
## Request binding
- `$TARGET`: bind the target named in the request.
## Runtime values
- Resolve `TARGET` from the request.
## Source compliance
- Required: use the read-only interface in Step 1.
## Steps
```bash
inspect-target "$TARGET"
```
## Verification
- Confirm the named target was inspected.
"""
            remembered = subprocess.run(
                [str(CLI), "remember", "--key", "demo.inspect",
                 "--source", str(source), "--procedure-file", "-"],
                input=procedure, capture_output=True, text=True, check=True,
                env={
                    **os.environ,
                    "PLAN_EXECUTE_CACHE": "1",
                    "PLAN_EXECUTE_CACHE_HOME": home,
                    "PLAN_EXECUTE_CACHE_REPO_ROOT": str(TOOL.parents[1]),
                },
            )
            self.assertIn("remembered", remembered.stdout)
            output = json.loads(self.call(home).stdout)
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertIn("demo.inspect", context)
            self.assertIn("Good: `media.ask.remote-source.backend-a`", context)
            self.assertIn("Recall before rediscovering", context)
            self.assertIn("The inventory is authoritative", context)
            self.assertIn("same requested outcome", context)
            self.assertIn("used only for a subtask", context)
            self.assertIn("Never call `recall` with an invented key", context)
            self.assertIn("recall --key", context)
            self.assertIn("form a compact working plan in\ncontext", context)
            self.assertIn("Do not\n   spend a separate tool call writing", context)
            self.assertIn("revise only the remaining checkpoints", context)
            self.assertIn("distill the shortest reusable procedure", context)
            self.assertIn("preserve mandatory and forbidden source rules", context)
            self.assertIn("Preconditions and constraints", context)
            self.assertIn("remember --key", context)
            self.assertIn("--procedure-file - <<'PROCEDURE'", context)
            self.assertIn("Never remember failed or\nblocked work", context)
            self.assertIn("may repeat plan, execute, and inspect", context)
            self.assertNotIn("begin --key", context)
            self.assertNotIn("update --working-id", context)
            self.assertNotIn("working-id", context)
            self.assertNotIn("memory_candidate", context)
            self.assertNotIn("{{PLAN_EXEC_CACHE}}", context)

    def test_empty_inventory_exposes_no_recall_command(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            output = json.loads(self.call(home).stdout)
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertIn("Cache miss already established", context)
            self.assertIn("Do not call `recall`", context)
            self.assertNotIn("recall --key", context)

    def test_only_session_start_hook_is_registered(self) -> None:
        settings = json.loads(SETTINGS.read_text())
        self.assertEqual(set(settings["hooks"]), {"SessionStart"})


if __name__ == "__main__":
    unittest.main()
