# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""C2 freeze manifest for Evaluation V2 (build or verify). Offline: no model, network or database.

    uv run python scripts/v2_freeze_manifest.py build  --data-root /home/hengz/evalv2-data [--pilot /home/hengz/vss-wt-pilot]
    uv run python scripts/v2_freeze_manifest.py verify --data-root /home/hengz/evalv2-data [--pilot ...]

The manifest pins every input of the C3 runs by hash:
- code: the eval tree, the contract, the prompt registry, the judge prompts and registry entry, the scorer files;
- data: every registered dataset the C3 configs name (each file re-hashed on disk) and the chat references;
- the pre-registration;
- the five C3 configs, hashed with ``freeze_manifest`` blanked (the configs then name this manifest's SHA-256);
- Track B's historical agent: the checkout commit and trees, the launcher, config, ffmpeg and nat environment, and
  C1b's pinned identity, which seeds the C3 Track B run;
- the budgets, and an attestation that no test-half run exists yet.
``verify`` rebuilds everything and fails on any difference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parents[1]
PROFILE = EVAL.parent
REPO = PROFILE.parents[3]
MANIFEST = EVAL / "methodology" / "v2-c2-freeze-manifest.json"
C3 = EVAL / "experiments" / "v2" / "c3"
CONFIGS = ("c3-track-a-switchyard", "c3-track-a-openrouter", "c3-track-b-historical", "c3-dashboard", "c3-chat")
SCORER_FILES = ("evalv2/score_stage.py", "evalv2/tasks/incident_objects/aggregate.py", "evalv2/tasks/incident_objects/v1.py",
                "evalv2/severity_report.py", "evalv2/track_a_report.py", "evalv2/cluster_bootstrap.py",
                "evalv2/tasks/dashboard/scoring.py", "evalv2/tasks/dashboard/report.py", "evalv2/references/dashboard_figures.py",
                "evalv2/tasks/chat/scoring.py", "evalv2/tasks/chat/evaluate.py", "evalv2/tasks/judges.py",
                "evalv2/tasks/judging.py", "evalv2/requests/chart.py", "evalv2/requests/judge.py", "evalv2/requests/vss.py",
                "evalv2/adapters/vss.py", "evalv2/executor.py")
PILOT_FILES = ("deploy/docker/developer-profiles/dev-profile-incident/eval/experiments/agent/start-experiment-agent.sh",
               "deploy/docker/developer-profiles/dev-profile-incident/eval/experiments/agent/experiment-agent.yml",
               ".tools/ffmpeg/bin/ffmpeg", ".tools/ffmpeg/bin/ffprobe")
HISTORICAL_COMMIT = "acbf892ba98a609c76d168ccd70d1b327917ceef"
USD_CAP = 2.00


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(Path(path).read_bytes())


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def config_content(path: Path) -> tuple[dict, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    blank = dict(raw, freeze_manifest=None)
    return raw, sha_bytes(json.dumps(blank, sort_keys=True, separators=(",", ":")).encode())


def build(data_root: Path, pilot: Path) -> dict:
    sys.path.insert(0, str(EVAL))
    from evalv2.datasets.registry import entry, resolve
    from evalv2.tasks.judges import load_judges, prompt_sha256

    configs, datasets, caps = {}, {}, {}
    for name in CONFIGS:
        raw, content = config_content(C3 / f"{name}.json")
        configs[name] = {"experiment": raw["experiment"], "content_sha256": content, "half": raw["split"]["half"],
                         "purpose": raw["purpose"], "subjects": [s["id"] for s in raw["subjects"]],
                         "models": sorted({s["model"] for s in raw["subjects"]}), "repeats": raw["repeats"]}
        caps[name] = [c for c in raw["caps"] if c["subject"] == "*"]
        for ds in raw.get("datasets", {}).values():
            if ds in datasets:
                continue
            item = entry(ds)
            resolved = resolve(ds, data_root_path=data_root)  # re-hashes every registered file on disk
            datasets[ds] = {"kind": item["kind"], "files": item["files"], "verified_on_disk": resolved is not None}
        chat = (raw["tasks"].get("chat") or {}).get("options", {}).get("references")
        if chat:
            actual = sha_file(data_root / chat["path"])
            assert actual == chat["sha256"], f"chat references {chat['path']} changed"
            datasets["chat-references-v1"] = {"kind": "chat_references", "files": {"records.jsonl": actual}, "verified_on_disk": True}
    judge = load_judges().get("openrouter-gpt-oss-20b-c3-v1")
    contract = {f: sha_file(PROFILE / "contracts" / f) for f in ("VERSION", "incident_extraction_prompt.txt",
                                                                 "incident_report.schema.json", "inference.json")}
    registry = json.loads((PROFILE / "contracts/prompts/registry.json").read_text())
    pilot_head = git(pilot, "rev-parse", "HEAD")
    assert pilot_head == HISTORICAL_COMMIT, f"historical checkout is at {pilot_head}"
    assert git(pilot, "status", "--porcelain") == "", "historical checkout is dirty"
    c1b_pin = json.loads((data_root / "v2/runs/c1b/agent.jsonl").read_text().splitlines()[-1])["pinned"]
    venv = Path.home() / ".cache/vss-agent-venv"
    freeze = subprocess.run([str(venv / "bin/python"), "-m", "pip", "freeze"], capture_output=True, text=True, check=True).stdout
    freeze = "\n".join(sorted(line for line in freeze.splitlines() if not line.startswith("-e") and "@ file" not in line)) + "\n"
    runs = data_root / "v2/runs"
    test_runs = sorted(p.name for p in runs.iterdir() if p.is_dir() and (p / "manifest.start.json").exists()
                       and json.loads((p / "manifest.start.json").read_text()).get("half") == "test")
    c3_dirs = sorted(p.name for p in runs.iterdir() if p.name.startswith("c3-"))
    return {
        "manifest_version": "evalv2-freeze-manifest-v1",
        "study": "Evaluation V2, C2 freeze for C3 (test half, option B: the 55 previously exposed clips)",
        "code": {"repo_head": git(REPO, "rev-parse", "HEAD"), "eval_tree": git(REPO, "rev-parse", "HEAD:deploy/docker/developer-profiles/dev-profile-incident/eval"),
                 "contract_version": (PROFILE / "contracts/VERSION").read_text().strip(), "contract_files_sha256": contract,
                 "prompt_registry_sha256": sha_file(PROFILE / "contracts/prompts/registry.json"),
                 "prompts": {e["id"]: e["sha256"] for e in registry["prompts"]},
                 "scorer_files_sha256": {f: sha_file(EVAL / f) for f in SCORER_FILES},
                 "judges_json_sha256": sha_file(EVAL / "evalv2/tasks/judges.json")},
        "preregistration_sha256": sha_file(EVAL / "methodology/evaluation-v2-preregistration.md"),
        "datasets": datasets,
        "configs": configs,
        "judge": {"id": judge.id, "model": judge.model, "endpoint": judge.endpoint, "settings": judge.settings,
                  "routing": ((getattr(judge, "request", None) or {}).get("extra_body") or {}).get("provider"),
                  "caps": {"max_requests": judge.max_requests, "max_tokens": judge.max_tokens},
                  "prompts_sha256": {k: prompt_sha256(k) for k in judge.kinds}, "pass_threshold": 0.5,
                  "status": "PROVISIONAL AI-judged: not human-calibrated (AI-consensus kappa 0.63 on incident fields)"},
        "track_b_historical": {"commit": pilot_head, "checkout_clean": True,
                               "agent_src_tree": git(pilot, "rev-parse", f"{HISTORICAL_COMMIT}:services/agent/src"),
                               "agent_contract_tree": git(pilot, "rev-parse", f"{HISTORICAL_COMMIT}:services/agent/src/vss_agents/incident_contract"),
                               "files_sha256": {f: sha_file(pilot / f) for f in PILOT_FILES},
                               "nat_env": {"path": "~/.cache/vss-agent-venv", "python": subprocess.run([str(venv / "bin/python"), "--version"], capture_output=True, text=True, check=True).stdout.strip(),
                                           "freeze_sha256": sha_bytes(freeze.encode()), "packages": len(freeze.splitlines())},
                               "c1b_pinned_identity": c1b_pin,
                               "label": "Track B (historical C1b profile): NOT the current production configuration"},
        "budgets": {"usd_hard_cap": USD_CAP, "run_wide_caps": caps,
                    "expected": {"calls": 3300, "tokens": 12_000_000, "usd": 0.70, "runtime_hours": "about 12 sequential; about 5 with Switchyard and OpenRouter queues in parallel"}},
        "attestation": {"test_half_runs_existing": test_runs, "c3_run_dirs_existing": c3_dirs,
                        "statement": "No test-half (C3) run exists and no held-out evaluation result has been produced or inspected at the time of this manifest."},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("build", "verify"))
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--pilot", type=Path, default=Path("/home/hengz/vss-wt-pilot"))
    a = ap.parse_args()
    m = build(a.data_root, a.pilot)
    if a.mode == "build":
        assert not m["attestation"]["test_half_runs_existing"] and not m["attestation"]["c3_run_dirs_existing"], m["attestation"]
        MANIFEST.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"written": str(MANIFEST), "sha256": sha_file(MANIFEST)}))
        return 0
    old = json.loads(MANIFEST.read_text(encoding="utf-8"))
    keys = [k for k in old if k not in ("attestation",)]
    drift = {k: (old[k], m[k]) for k in keys if old[k] != m[k] and k != "code"}
    code_drift = {k: (old["code"][k], m["code"][k]) for k in old["code"] if k not in ("repo_head", "eval_tree") and old["code"][k] != m["code"][k]}
    if drift or code_drift:
        print(json.dumps({"ok": False, "drift": list(drift), "code_drift": list(code_drift)}, indent=1))
        return 1
    print(json.dumps({"ok": True, "manifest_sha256": sha_file(MANIFEST), "repo_head_now": m["code"]["repo_head"],
                      "repo_head_at_build": old["code"]["repo_head"], "attestation_now": m["attestation"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
