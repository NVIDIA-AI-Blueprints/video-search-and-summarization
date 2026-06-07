#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run ONE (skill, paradigm) review leg and write a canonical-schema artifact.

Mirrors .github/skill-eval/skills_eval_agent.py's substrate: a single
synchronous Claude Agent SDK session per leg (parallelism comes from the
Actions matrix, not in-agent subagents). Each paradigm reads only the repo
checkout skills/<skill>/ and emits review-<slug>.json in the canonical schema
(findings-schema.json) via review_findings.

Paradigm engines (KTD4):
  review / gstack-review / best-practices  -> Claude Agent SDK + a vendored rubric
  ce-code-review / ce-doc-review           -> the installed CE skill (headless JSON)
  codex                                    -> codex exec (degrades if codex absent)

Invariant: a leg ALWAYS writes a schema-valid artifact (status ok|degraded|
skipped|failed) and exits 0 — advisory CI never fails on a review leg.

Env:
  EVAL_SKILL, EVAL_PARADIGM   the one leg to run
  REVIEW_OUT_DIR              where review-<slug>.json is written (default: cwd)
  PR_BASE                     merge base ref (for diff-based paradigms)
  ANTHROPIC_* / GH_TOKEN      sourced from the coordinator .env by the workflow
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import review_findings as rf  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
PROMPTS_DIR = HERE / "prompts"

# Diff-based paradigms review the PR change; whole-read paradigms read the
# entire skill dir (skill-authoring quality lives in the full file, not a diff).
DIFF_BASED = {"review", "gstack-review", "codex", "ce-code-review"}
WHOLE_READ = {"ce-doc-review", "best-practices"}
# Which engine drives each paradigm.
ENGINE = {
    "review": "claude", "gstack-review": "claude", "best-practices": "claude",
    "ce-code-review": "ce_skill", "ce-doc-review": "ce_skill", "codex": "codex",
}


class SkippedLeg(Exception):
    """Raised when a paradigm cannot run in this environment (degrade, don't fail)."""


def prompt_for(paradigm: str) -> str:
    return (PROMPTS_DIR / f"{paradigm}.md").read_text()


def _json_blocks(text: str) -> list[str]:
    """All balanced top-level {...} / [...] blocks (string-aware brace matching)."""
    blocks, depth, start, instr, esc = [], 0, None, False, False
    for i, ch in enumerate(text):
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
            continue
        if ch == '"':
            instr = True
        elif ch in "{[":
            if depth == 0:
                start = i
            depth += 1
        elif ch in "}]" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                blocks.append(text[start:i + 1])
                start = None
    return blocks


def extract_findings(text: str) -> list[dict]:
    """Pull the findings list out of an agent's final message.

    Accepts either a `{"findings": [...]}` object or a bare `[...]` array,
    scanning balanced JSON blocks from the end so a trailing/revised block
    wins over an earlier one or surrounding prose.
    """
    for block in reversed(_json_blocks(text)):
        try:
            obj = json.loads(block)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("findings"), list):
            return obj["findings"]
        if isinstance(obj, list):
            return obj
    return []


# ── engines (overridable for tests) ─────────────────────────────────────

def _engine_claude(paradigm: str, skill: str, skill_dir: pathlib.Path,
                   base_ref: str) -> list[dict]:
    """Run a vendored-rubric paradigm via the Claude Agent SDK (one serial session)."""
    try:
        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions  # type: ignore
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                        "claude-agent-sdk>=0.0.5"], check=True)
        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions  # type: ignore

    os.environ.setdefault("CLAUDE_CODE_DISABLE_THINKING", "1")  # NVIDIA proxy rejects otherwise
    system = prompt_for(paradigm)
    scope = (f"Diff this skill against {base_ref} and review the change."
             if paradigm in DIFF_BASED else "Review the whole skill directory.")
    user = (f"Skill: {skill}\nSkill dir: {skill_dir}\n{scope}\n"
            f"Return ONLY a JSON object {{\"findings\": [...]}} per the schema.")
    options = ClaudeAgentOptions(
        system_prompt=system,
        allowed_tools=["Read", "Grep", "Glob", "Bash"],
        disallowed_tools=["TaskCreate", "TaskUpdate", "TaskGet", "TaskList",
                          "TaskOutput", "TaskStop", "BashOutput", "KillShell"],
        permission_mode="bypassPermissions",
        cwd=str(REPO_ROOT),
    )
    text_parts: list[str] = []
    import anyio  # claude-agent-sdk dependency

    async def _run():
        async with ClaudeSDKClient(options=options) as client:
            await client.query(user)
            async for msg in client.receive_response():
                for block in getattr(msg, "content", []) or []:
                    t = getattr(block, "text", None)
                    if t:
                        text_parts.append(t)

    anyio.run(_run)
    return extract_findings("\n".join(text_parts))


def _engine_ce_skill(paradigm: str, skill: str, skill_dir: pathlib.Path,
                     base_ref: str) -> list[dict]:
    """Run an installed compound-engineering review skill headless and parse its JSON."""
    from shutil import which
    if which("claude") is None:
        raise SkippedLeg("claude CLI / CE plugin not installed on this runner")
    if paradigm == "ce-code-review":
        args = f"mode:agent base:{base_ref}"
    else:  # ce-doc-review reviews the SKILL.md as a document
        skillmd = skill_dir / "SKILL.md"
        if not skillmd.is_file():
            raise SkippedLeg(f"{skill}/SKILL.md not found for ce-doc-review")
        args = f"mode:headless {skillmd}"
    proc = subprocess.run(
        ["claude", "-p", f"/{paradigm} {args}"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        env={**os.environ, "CLAUDE_CODE_DISABLE_THINKING": "1"},
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise SkippedLeg(f"{paradigm} returned {proc.returncode}: "
                         f"{proc.stderr.strip()[:200]}")
    return extract_findings(proc.stdout)


def _engine_codex(paradigm: str, skill: str, skill_dir: pathlib.Path,
                  base_ref: str) -> list[dict]:
    """Run the codex adversary review (degrades to skipped if codex is absent)."""
    from shutil import which
    if which("codex") is None:
        raise SkippedLeg("codex CLI not installed on this runner")
    prompt = prompt_for("codex")
    proc = subprocess.run(
        ["codex", "exec", prompt, "-C", str(REPO_ROOT), "-s", "read-only",
         "--skip-git-repo-check", "-c", 'model_reasoning_effort="high"'],
        capture_output=True, text=True, env={**os.environ, "SPAWNED_SESSION": "1"},
        stdin=subprocess.DEVNULL,
    )
    return extract_findings(proc.stdout)


ENGINE_FN = {"claude": _engine_claude, "ce_skill": _engine_ce_skill, "codex": _engine_codex}


def run_leg(skill: str, paradigm: str, *, base_ref: str = "origin/develop") -> dict:
    """Run one leg; always return a schema-valid artifact dict."""
    skill_dir = REPO_ROOT / "skills" / skill
    status, findings = "ok", []
    try:
        if not skill_dir.is_dir():
            raise SkippedLeg(f"skills/{skill}/ not found on this ref")
        raw = ENGINE_FN[ENGINE[paradigm]](paradigm, skill, skill_dir, base_ref)
        findings = rf.normalize(paradigm, raw, skill=skill)
    except SkippedLeg as exc:
        status = "skipped"
        print(f"::warning::skills-review {skill}·{paradigm}: skipped — {exc}", flush=True)
    except Exception as exc:  # advisory: a broken leg never fails the job
        status = "failed"
        print(f"::warning::skills-review {skill}·{paradigm}: failed — {exc!r}", flush=True)
    return {"skill": skill, "paradigm": paradigm, "status": status, "findings": findings}


def main() -> int:
    skill = os.environ["EVAL_SKILL"]
    paradigm = os.environ["EVAL_PARADIGM"]
    base_ref = os.environ.get("PR_BASE", "origin/develop")
    out_dir = pathlib.Path(os.environ.get("REVIEW_OUT_DIR", "."))
    out_dir.mkdir(parents=True, exist_ok=True)

    artifact = run_leg(skill, paradigm, base_ref=base_ref)
    out = out_dir / f"review-{skill}__{paradigm}.json"
    out.write_text(json.dumps(artifact, indent=2) + "\n")
    print(f"skills-review {skill}·{paradigm}: status={artifact['status']} "
          f"findings={len(artifact['findings'])} -> {out}")
    return 0  # advisory


if __name__ == "__main__":
    sys.exit(main())
