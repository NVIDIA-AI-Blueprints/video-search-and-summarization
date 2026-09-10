# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Box-side probe recording what the VIOS streamprocessing container cost to start.

Prebake (#1798) moves a 224-package apt install out of container start and into
image build. Turning it on was easy; proving it did anything was not, because
the deploy runs on the box and none of its output reaches the CI job log or the
trial artifact. Measured on an L40 the install takes 116.3 s and the prebaked
path takes 0.17 s, so the effect is large and was still invisible.

This closes that. It reads the numbers off the container itself, writes them
where harbor already collects, and emits both the timing and the container's own
verdict, so a reader never has to infer whether prebake was active.
"""

from __future__ import annotations

import shlex

SINK_DEFAULT = "/logs/artifacts/vios-ready.log"
CONTAINER = "vss-vios-streamprocessing"


def build_probe_command(label: str, container: str = CONTAINER) -> str:
    """POSIX-sh emitting one `VIOSPROBE <label> ...` line, then `scan=complete`.

    The trailing line always prints, so "the probe did not run" is
    distinguishable from "the container was not there" -- a profile that never
    activates VIOS is silent about prebake and must not read as a zero.

    Output is tee'd to stdout and to a durable sink, because brev_env's Python
    logging is swallowed by harbor. The default sink is collected into
    `<trial>/artifacts/logs/artifacts/vios-ready.log`.
    """
    lbl, cnt = shlex.quote(label), shlex.quote(container)
    return f"""set +e
SINK="${{VIOSPROBE_SINK:-{SINK_DEFAULT}}}"
mkdir -p "$(dirname "$SINK")" 2>/dev/null
LABEL={lbl}
CNT={cnt}
{{
if docker inspect "$CNT" >/dev/null 2>&1; then
  started=$(docker inspect -f '{{{{.State.StartedAt}}}}' "$CNT" 2>/dev/null)
  image=$(docker inspect -f '{{{{.Config.Image}}}}' "$CNT" 2>/dev/null)
  health=$(docker inspect -f '{{{{if .State.Health}}}}{{{{.State.Health.Status}}}}{{{{else}}}}none{{{{end}}}}' "$CNT" 2>/dev/null)
  # The container's own words. "skipping APT" is the prebaked path; "Installation
  # completed" is the install actually running. Counting both means a log we
  # failed to read is reported as unknown rather than as either verdict.
  logs=$(docker logs "$CNT" 2>&1 | head -400)
  skipped=$(printf '%s' "$logs" | grep -ci 'already installed; skipping APT')
  installed=$(printf '%s' "$logs" | grep -ci 'Installation completed successfully')
  if [ "$skipped" -gt 0 ]; then verdict=prebaked
  elif [ "$installed" -gt 0 ]; then verdict=installed-at-start
  else verdict=unknown; fi
  case "$image" in *prebaked*) tagged=yes ;; *) tagged=no ;; esac
  echo "VIOSPROBE $LABEL container=present verdict=$verdict image_prebaked=$tagged health=$health started=$started"
else
  echo "VIOSPROBE $LABEL container=absent"
fi
echo "VIOSPROBE $LABEL scan=complete"
}} 2>&1 | tee -a "$SINK"
exit 0
"""


def parse_probe_lines(stdout: str) -> list[dict]:
    """`VIOSPROBE` lines -> dicts. Ignores everything else on the stream."""
    out = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("VIOSPROBE "):
            continue
        parts = line.split()[1:]
        d = {"label": parts[0]} if parts else {}
        for kv in parts[1:]:
            if "=" in kv:
                k, _, v = kv.partition("=")
                d[k] = v
        if len(d) > 1:
            out.append(d)
    return out
