# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
NEMOCLAW_NOTEBOOK = REPO_ROOT / "deploy/docker/scripts/deploy_nemoclaw.ipynb"
ORCHESTRATOR_NOTEBOOK = (
    REPO_ROOT / "deploy/docker/scripts/deploy_vss_orchestrator.ipynb"
)
HARBOR_ADAPTER = REPO_ROOT / ".github/skill-eval/nemoclaw/notebook_setup_adapter.py"


def _notebook(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):  # pragma: no cover - checked-in invariant
        raise TypeError(f"notebook is not an object: {path}")
    return payload


def _source(cell: dict[str, Any]) -> str:
    value = cell.get("source", "")
    return value if isinstance(value, str) else "".join(value)


class ExternalAgentNotebookContractTest(unittest.TestCase):
    def test_dedicated_notebook_installs_the_recursive_catalog_and_receipt(
        self,
    ) -> None:
        notebook = _notebook(NEMOCLAW_NOTEBOOK)
        source = "\n".join(_source(cell) for cell in notebook["cells"])

        self.assertIn('SKILLS_DIR.rglob("SKILL.md")', source)
        self.assertNotIn('SKILLS_DIR.glob("*/SKILL.md")', source)
        self.assertIn('git -C "$runtime_dir" checkout --detach', source)
        self.assertIn("--extra cli vss --version", source)
        self.assertIn('"identity_mode": "dedicated"', source)
        self.assertIn('"version": "1.0"', source)
        self.assertIn("/sandbox/.vss/agent-capabilities.json", source)

    def test_orchestrator_passes_a_commit_bound_receipt_to_the_gateway(self) -> None:
        notebook = _notebook(ORCHESTRATOR_NOTEBOOK)
        gateway_cell = next(
            cell
            for cell in notebook["cells"]
            if "_fetch_agent_capabilities" in _source(cell)
        )
        source = _source(gateway_cell)

        compile(source, f"{ORCHESTRATOR_NOTEBOOK}:agent-gateway", "exec")
        self.assertIn("/sandbox/.vss/agent-capabilities.json", source)
        self.assertIn('"VSS_AGENT_GATEWAY_REQUIRE_CAPABILITIES": "true"', source)
        self.assertIn("VSS_AGENT_GATEWAY_CAPABILITIES_SHA256", source)
        self.assertIn("VSS_AGENT_GATEWAY_EXPECTED_RUNTIME_REF", source)
        self.assertIn('"VSS_AGENT_BACKEND_PROTOCOL": "responses"', source)

    def test_harbor_nemoclaw_setup_executes_both_checked_in_notebooks(self) -> None:
        source = HARBOR_ADAPTER.read_text(encoding="utf-8")

        first = source.index('Path("deploy/docker/scripts/deploy_nemoclaw.ipynb")')
        second = source.index(
            'Path("deploy/docker/scripts/deploy_vss_orchestrator.ipynb")'
        )
        self.assertLess(first, second)
        self.assertIn("nemoclaw = execute_notebook(paths[0]", source)
        self.assertIn("orchestrator = execute_notebook(paths[1]", source)


if __name__ == "__main__":
    unittest.main()
