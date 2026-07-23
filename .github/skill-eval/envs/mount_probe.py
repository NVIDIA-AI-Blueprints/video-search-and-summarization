# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bind-mount liveness probe for the skill-eval harness.

Diagnostic used to PROVE (or disprove) the lvs_profile_summarize step-2
failure mechanism: between steps, the harness `git clean` can delete
`deploy/docker/data-dir/` — the host source of the VIOS upload bind
mounts — while step-1's containers keep running, leaving them pinned to
an unlinked inode. A before/after pair around the repo sync turns "we
infer it from a flaky reward" into a logged fact.

The probe SELF-DISCOVERS the relevant mounts (it does not guess a
container name): it scans every running container for bind mounts whose
host source is a VIOS data path (`.../data_log/vst*`, `clip_storage`,
`vst_data`, `streamer_videos`, ...) and, for each, compares the host
source inode against the container's view. Robust to inode-number reuse
because it also checks the container-side link count (a host `rm -rf`
leaves the container pinned to an unlinked inode → link count 0) and
writability.

Split out of brev_env.py so the verdict logic + line parsing are unit-
testable with no Brev box and no harbor import, and so the exact same
box-side shell is exercised by envs/tests/prove_mount_probe.sh against a
real Docker bind mount.

Verdicts (per discovered mount):
    healthy        host + container agree on the inode, links > 0, writable
    stale          container pinned to a different/unlinked inode, or RO
    absent-source  host bind source is gone (deleted, not recreated)
"""
from __future__ import annotations

import shlex

_MARKER = "MOUNTPROBE"

# Host-source glob patterns that identify a VIOS bind mount worth probing.
# Kept in the shell below; documented here for readers/tests.
VIOS_SOURCE_PATTERNS = (
    "*/data_log/vst*", "*/clip_storage", "*/vst_data",
    "*/vst_video", "*/temp_files", "*/streamer_videos*",
)


def classify_mount(
    *,
    container_exists: bool,
    source_exists: bool,
    host_inode: str | None,
    container_inode: str | None,
    container_links: str | None,
    writable: bool,
) -> str:
    """Pure reference verdict from the observed facts. The box-side shell
    mirrors this; prove_mount_probe.sh keeps them from drifting."""
    if not container_exists:
        return "no-container"
    if not source_exists:
        return "absent-source"
    # A missing container-side inode reading means stat failed on the view
    # the upload writes to — treat as stale, never healthy.
    if not container_inode:
        return "stale"
    if container_links is not None and container_links.strip() == "0":
        return "stale"
    if host_inode and host_inode.strip() != container_inode.strip():
        return "stale"
    # NOT-writable is deliberately NOT treated as stale: some VIOS mounts are
    # read-only by design (e.g. the RT-VLM's clip_storage), and the deletion we
    # hunt is already caught by links==0 / missing-or-mismatched host_inode
    # above. `writable` is reported on the line for context only.
    return "healthy"


def build_probe_command(label: str) -> str:
    """Return the box-side POSIX-sh probe that emits one `MOUNTPROBE
    <label> verdict=... ` line per discovered VIOS bind mount, plus a
    trailing `MOUNTPROBE <label> scan=complete` line (fail-loud: the
    trailing line always prints, so 'no output' is distinguishable from
    'no VIOS mounts found'). Never exits non-zero.

    Output is tee'd to BOTH stdout and a durable sink. brev_env's Python
    `logging` is swallowed by harbor (never reaches the CI job log or the
    collected artifact), so the sink is the only reliable channel: the
    default `/logs/artifacts/mount-probe.log` is collected by harbor into
    `<trial>/artifacts/logs/artifacts/mount-probe.log` in the downloadable
    results tarball. Override via $MOUNTPROBE_SINK (the local Docker
    self-test points it at a tmp path; use /dev/null to disable)."""
    lbl = shlex.quote(label)
    return f"""set +e
SINK="${{MOUNTPROBE_SINK:-/logs/artifacts/mount-probe.log}}"
mkdir -p "$(dirname "$SINK")" 2>/dev/null
LABEL={lbl}
{{
for cid in $(docker ps -q 2>/dev/null); do
  cname=$(docker inspect -f '{{{{.Name}}}}' "$cid" 2>/dev/null | sed 's#^/##')
  docker inspect -f '{{{{range .Mounts}}}}{{{{if eq .Type "bind"}}}}{{{{.Source}}}}|{{{{.Destination}}}}{{{{"\\n"}}}}{{{{end}}}}{{{{end}}}}' "$cid" 2>/dev/null | while IFS='|' read -r src dst; do
    [ -z "$src" ] && continue
    case "$src" in
      */data_log/vst*|*/clip_storage|*/vst_data|*/vst_video|*/temp_files|*/streamer_videos*) : ;;
      *) continue ;;
    esac
    if [ -d "$src" ]; then srcex=1; hi=$(stat -Lc '%i' "$src" 2>/dev/null); else srcex=0; hi=; fi
    ci=$(docker exec "$cid" stat -Lc '%i' "$dst" 2>/dev/null)
    cl=$(docker exec "$cid" stat -Lc '%h' "$dst" 2>/dev/null)
    if docker exec "$cid" test -w "$dst" 2>/dev/null; then w=1; else w=0; fi
    v=healthy
    [ "$srcex" = 0 ] && v=absent-source
    [ -z "$ci" ] && v=stale
    [ "$cl" = "0" ] && v=stale
    [ -n "$hi" ] && [ -n "$ci" ] && [ "$hi" != "$ci" ] && v=stale
    echo "{_MARKER} $LABEL verdict=$v container=$cname source=$src dest=$dst host_inode=$hi container_inode=$ci links=$cl writable=$w"
  done
done
echo "{_MARKER} $LABEL scan=complete"
}} 2>&1 | tee -a "$SINK"
"""


def parse_probe_lines(text: str) -> list[dict]:
    """Return one dict per `MOUNTPROBE ...` line (verdict lines and the
    trailing scan=complete marker). Each dict has at least {label}."""
    out: list[dict] = []
    for raw in (text or "").splitlines():
        raw = raw.strip()
        if not raw.startswith(_MARKER + " "):
            continue
        toks = raw.split()
        d: dict[str, str] = {"label": toks[1] if len(toks) > 1 else ""}
        for tok in toks[2:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                d[k] = v
        out.append(d)
    return out


def parse_probe_line(text: str) -> dict | None:
    """Last verdict-bearing MOUNTPROBE line (ignores scan=complete), or
    None. Convenience for single-mount tests / callers."""
    verdicts = [d for d in parse_probe_lines(text) if "verdict" in d]
    return verdicts[-1] if verdicts else None
