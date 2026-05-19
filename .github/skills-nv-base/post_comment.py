#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Post or update a sticky PR comment summarising the Skills NV-BASE gate.

Reads two JSON inputs:
  - $NVBASE_FINDINGS_JSON     (written by run_check.py)
  - $PLAYBOOK_FINDINGS_JSON   (written by skill_compliance_check.py --json-out)

Composes a single markdown comment carrying a hidden HTML marker so we can
match it across runs. If a previous comment with the same marker exists,
PATCH it in place; otherwise POST a new one. Always posts, even when both
inputs report zero findings (the green check is useful signal).

Env vars required (push mode):
  GITHUB_TOKEN           - auth (provided by Actions)
  GITHUB_REPOSITORY      - owner/repo
  PR_NUMBER              - PR to comment on

Manual-mode fallback (workflow_dispatch):
  When PR_NUMBER is empty (no PR to comment on), the same composed
  markdown is appended to $GITHUB_STEP_SUMMARY instead. The Actions run
  summary page is then the operator's single place to see findings.

Optional:
  GITHUB_SHA             - commit SHA (for the comment footer)
  GITHUB_RUN_ID          - workflow run id (for the "view log" link)
  GITHUB_SERVER_URL      - usually https://github.com

Stdlib only. Errors are surfaced as ::warning annotations and the script
exits 0 — failing here should not fail the gate.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

MARKER = "<!-- skills-nv-base-bot:v1 -->"
MAX_ROWS_PER_TABLE = 30


# ── GitHub REST helpers ────────────────────────────────────────────────


def _gh_request(method: str, url: str, body: Optional[dict] = None) -> dict:
    token = os.environ["GITHUB_TOKEN"]
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req) as resp:
        if resp.status == 204:
            return {}
        return json.loads(resp.read())


def find_sticky_comment(repo: str, pr: str) -> Optional[int]:
    """Return the id of the existing sticky comment, or None."""
    url = f"https://api.github.com/repos/{repo}/issues/{pr}/comments?per_page=100"
    while url:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(req) as resp:
            comments = json.loads(resp.read())
            link = resp.headers.get("Link") or ""
        for c in comments:
            if MARKER in (c.get("body") or ""):
                return c["id"]
        url = None
        # rfc5988 link header; pick rel="next" if present
        for piece in link.split(","):
            if 'rel="next"' in piece:
                start = piece.find("<") + 1
                end = piece.find(">", start)
                if start > 0 and end > start:
                    url = piece[start:end]
                break
    return None


def upsert_comment(repo: str, pr: str, body: str) -> None:
    existing = find_sticky_comment(repo, pr)
    if existing is not None:
        _gh_request(
            "PATCH",
            f"https://api.github.com/repos/{repo}/issues/comments/{existing}",
            {"body": body},
        )
        print(f"::notice::Updated sticky comment id={existing}", flush=True)
    else:
        _gh_request(
            "POST",
            f"https://api.github.com/repos/{repo}/issues/{pr}/comments",
            {"body": body},
        )
        print("::notice::Posted new sticky comment", flush=True)


# ── Markdown composition ───────────────────────────────────────────────

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4,
             "ERROR": 0, "WARNING": 1, "INFO": 2, "PASS": 3}
SEV_EMOJI = {
    "critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "ℹ️",
    "ERROR": "🔴", "WARNING": "🟡", "INFO": "ℹ️",
}


def _md_escape(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ").strip()


def _load_json(path_env: str) -> Optional[dict]:
    p = os.environ.get(path_env, "").strip()
    if not p:
        return None
    pth = Path(p)
    if not pth.is_file():
        print(f"::notice::{path_env}={p} not found; treating as no findings",
              flush=True)
        return None
    try:
        return json.loads(pth.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::warning::Could not parse {path_env}={p}: {exc}", flush=True)
        return None


def _section_nvbase(data: Optional[dict]) -> str:
    if data is None:
        return "### Step 1 — nv-base validate\n\n_Step did not run or no JSON output captured._\n"
    counts = data.get("counts", {})
    findings = data.get("findings", [])
    blocking = data.get("blocking", 0)

    summary = (
        f"**critical={counts.get('critical', 0)} · "
        f"high={counts.get('high', 0)} · "
        f"medium={counts.get('medium', 0)} · "
        f"low={counts.get('low', 0)}**"
    )
    status = "✅ no blocking findings" if blocking == 0 else f"❌ {blocking} blocking finding(s)"
    out = [f"### Step 1 — nv-base validate", "", f"{status} — {summary}", ""]
    if findings:
        findings = sorted(findings, key=lambda f: SEV_ORDER.get(f.get("severity", "info"), 9))
        out += [
            "| Sev | Check | File | Line | Message |",
            "|---|---|---|---:|---|",
        ]
        for f in findings[:MAX_ROWS_PER_TABLE]:
            sev = f.get("severity", "info")
            emoji = SEV_EMOJI.get(sev, "")
            out.append(
                f"| {emoji} {sev} "
                f"| `{_md_escape(f.get('validator', '') + '/' + f.get('check', ''))}` "
                f"| `{_md_escape(f.get('file', ''))}` "
                f"| {f.get('line', 0) or ''} "
                f"| {_md_escape(f.get('message', ''))} |"
            )
        if len(findings) > MAX_ROWS_PER_TABLE:
            out.append(f"\n_…and {len(findings) - MAX_ROWS_PER_TABLE} more (truncated; see job annotations for the full list)._")
    return "\n".join(out) + "\n"


def _section_playbook(data: Optional[dict]) -> str:
    if data is None:
        return "### Step 2 — playbook compliance\n\n_Step did not run or no JSON output captured._\n"
    findings = data.get("findings", [])
    skills_checked = data.get("skills_checked", 0)
    errors = [f for f in findings if f.get("severity") == "ERROR"]
    warnings = [f for f in findings if f.get("severity") == "WARNING"]

    status = "✅ no errors" if not errors else f"❌ {len(errors)} error(s)"
    summary = (
        f"**errors={len(errors)} · warnings={len(warnings)} · "
        f"skills_checked={skills_checked}**"
    )
    out = [f"### Step 2 — playbook compliance", "", f"{status} — {summary}", ""]
    if findings:
        findings = sorted(findings, key=lambda f: (
            SEV_ORDER.get(f.get("severity", "INFO"), 9),
            f.get("skill", ""), f.get("rule", ""),
        ))
        out += [
            "| Sev | Rule | Skill | File | Message |",
            "|---|---|---|---|---|",
        ]
        for f in findings[:MAX_ROWS_PER_TABLE]:
            sev = f.get("severity", "INFO")
            emoji = SEV_EMOJI.get(sev, "")
            file_field = _md_escape(f.get("file", "") or "")
            out.append(
                f"| {emoji} {sev} "
                f"| `{_md_escape(f.get('rule', ''))}` "
                f"| `{_md_escape(f.get('skill', ''))}` "
                f"| {('`' + file_field + '`') if file_field else ''} "
                f"| {_md_escape(f.get('message', ''))} |"
            )
        if len(findings) > MAX_ROWS_PER_TABLE:
            out.append(f"\n_…and {len(findings) - MAX_ROWS_PER_TABLE} more (truncated; see job log for the full list)._")
    return "\n".join(out) + "\n"


def _footer() -> str:
    sha = os.environ.get("GITHUB_SHA", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    bits = []
    if sha:
        bits.append(f"commit `{sha[:7]}`")
    if repo and run_id:
        bits.append(f"[view full log]({server}/{repo}/actions/runs/{run_id})")
    if not bits:
        return ""
    return "\n---\n_Skills NV-BASE — " + " · ".join(bits) + "._\n"


def compose_body(nv: Optional[dict], pb: Optional[dict]) -> str:
    return (
        f"{MARKER}\n"
        f"## 🤖 Skills NV-BASE\n\n"
        f"{_section_nvbase(nv)}\n"
        f"{_section_playbook(pb)}"
        f"{_footer()}"
    )


def _write_step_summary(body: str) -> bool:
    """Append the findings body to $GITHUB_STEP_SUMMARY.

    Returns True on success. Renders an ::warning annotation and returns
    False on any I/O failure — the gate should not fail on a summary
    write error.
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not path:
        return False
    try:
        # The trailing blank line keeps successive summary blocks from
        # collapsing into one paragraph if other steps also append.
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(body)
            if not body.endswith("\n"):
                fh.write("\n")
            fh.write("\n")
        return True
    except OSError as exc:
        print(f"::warning::Could not write GITHUB_STEP_SUMMARY ({path}): "
              f"{exc}", flush=True)
        return False


def main() -> None:
    pr = os.environ.get("PR_NUMBER", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "")

    nv = _load_json("NVBASE_FINDINGS_JSON")
    pb = _load_json("PLAYBOOK_FINDINGS_JSON")
    body = compose_body(nv, pb)

    # Manual-mode (workflow_dispatch): PR_NUMBER is empty. Route findings
    # to the Actions run summary page so the operator has a single place
    # to read results without a PR thread. Repo / token may still be
    # populated by Actions but are unused in this path.
    if not pr:
        if _write_step_summary(body):
            print("Manual mode: findings appended to $GITHUB_STEP_SUMMARY",
                  flush=True)
        else:
            print("::warning::PR_NUMBER unset and GITHUB_STEP_SUMMARY "
                  "unavailable; findings printed to stdout only",
                  flush=True)
            print(body, flush=True)
        return

    if not repo or not token:
        print("::warning::GITHUB_REPOSITORY or GITHUB_TOKEN missing; "
              "cannot post comment", flush=True)
        sys.exit(0)  # don't fail the gate

    try:
        upsert_comment(repo, pr, body)
    except urllib.error.HTTPError as exc:
        print(f"::warning::Could not post/update comment ({exc.code} "
              f"{exc.reason}): {exc.read().decode(errors='replace')[:200]}",
              flush=True)
    except Exception as exc:
        print(f"::warning::Comment poster failed: {exc!r}", flush=True)


if __name__ == "__main__":
    main()
