#!/usr/bin/env python3
"""Connect the procedure cache to Claude Code hooks."""

import json
import sys
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOL_ROOT))

from core import enabled  # noqa: E402
from memory import ProcedureStore  # noqa: E402


def inventory() -> list[str]:
    return [
        f"- {key}; description: {metadata.get('description', '')}"
        for key, metadata in ProcedureStore().inventory()
    ]


def recall_guidance(memories: list[str]) -> str:
    if not memories:
        return """## 1. Cache miss already established

No reusable procedures exist. Do not call `recall`; continue directly to the
miss workflow below.
"""
    return """## 1. Recall before rediscovering the procedure

Before loading instructions or executing, compare the request with the
available procedure descriptions. If one matches, call `recall` with that exact
listed key:

```bash
"{{PLAN_EXEC_CACHE}}" recall --key <listed-key>
```

The inventory is authoritative. Never call `recall` with an invented key. A
match requires the same requested outcome; sharing a file, service, or other
input is not enough. A procedure used only for a subtask is not a hit for the
whole request. If none matches, continue directly to the miss workflow.

On a hit, follow the returned procedure with normal tools. Resolve current
values from the request and live results, and verify the real outcome. Reload
source instructions only if execution shows that the procedure is stale.
"""


def session_start() -> None:
    memories = inventory()
    protocol = (
        (TOOL_ROOT / "instructions.md").read_text(encoding="utf-8")
        .replace("{{RECALL_GUIDANCE}}", recall_guidance(memories))
        .replace(
            "{{PLAN_EXEC_CACHE}}",
            str(TOOL_ROOT / "bin/plan-exec-cache"),
        )
    )
    context = (
        "Runtime procedural memory is active for tool-using requests.\n"
        "Available procedures (each one is checked again when recalled):\n"
        + ("\n".join(memories) if memories else "none")
        + "\n\n" + protocol
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))


def main() -> None:
    if not enabled():
        return
    session_start()


if __name__ == "__main__":
    main()
