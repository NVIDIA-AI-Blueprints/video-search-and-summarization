#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep every committed GPU device index reachable through the single-GPU clamp.

The profile env files under ``deploy/docker`` place each service on a specific
GPU, and those indices describe the host the profile was validated on rather
than the host it will run on:

  dev-profile-alerts    LLM, VLM and RT-VLM on device 1, RT-CV on 0   -> 2 GPUs
  dev-profile-search    RT-CV + RT-VLM on 0, RT-Embed + LLM on 1      -> 2 GPUs
  warehouse-operations  RT-CV on 0, RT-VLM on 1, LLM on 2             -> 3 GPUs

Compose passes those values straight into ``device_ids``, and a request for an
index the host does not have is a hard container-start failure, not a fallback.
So on a single-GPU machine the stack never comes up, and the reason is invisible
in the configuration: every value in the files is individually correct.

What this lint asserts is therefore not a value but a route. The deploy scripts
resolve committed placement against the GPU count the host reports
(``clamp_device_ids_to_gpu_count``), folding any index at or above that count
onto the last real device and logging every remap. This checks that:

  1. every device index committed in a profile parses as a device index at all;
  2. every key a profile uses to place a service is one the clamp knows about;
  3. the clamp is actually invoked, and is not gated behind one profile or one
     environment marker.

(3) is the shape the defect originally had. ``get_nvidia_smi_gpu_count`` already
existed in ``dev-profile.sh`` and was called from exactly one place: a
search-only, ``BREV_ENV_ID``-gated pre-flight. The helper was there, the general
case was not covered, and nothing in the tree said so.

Deliberately NOT asserted: that no profile commits an index above 0. A two-GPU
default is the validated configuration for alerts and search, and forbidding it
would push those profiles onto a layout nothing has been tested on. Nor is a
declared "this profile needs N GPUs" value required: the highest committed index
already is that number, so a second copy of it would only be something to keep
in sync. The requirement is reported below instead, derived from the files.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_SUFFIX = ".env"

# Bash arrays in the deploy scripts that enumerate the keys the clamp handles.
# Placement keys are remapped; reservation keys name GPUs the LLM and VLM must
# stay off and are filtered instead, so they are declared separately.
CLAMP_KEY_ARRAYS = ("DEVICE_ID_KEYS", "DEVICE_RESERVATION_KEYS")
CLAMP_FUNCTION = "clamp_device_ids_to_gpu_count"
GPU_COUNT_FUNCTIONS = ("get_deployment_gpu_count", "get_nvidia_smi_gpu_count")

# Any env key naming a GPU by index. Matched by suffix rather than listed, so a
# newly invented placement key is in scope the moment it is committed.
DEVICE_KEY_RE = re.compile(r"^[A-Z0-9_]*DEVICE_IDS?$")
ASSIGNMENT = re.compile(r"^\s*(?P<name>[A-Z0-9_]+)\s*=\s*(?P<value>.*)$")

# An enclosing condition mentioning any of these means the clamp only runs for
# one profile or one kind of host, which is the failure this lint exists for.
# ``profile ==``/``deployment ==`` pin the run to a single profile;
# BREV_ENV_ID and SKIP_HARDWARE_CHECK pin it to one environment.
SCOPING_CONDITIONS = (
    re.compile(r"\$\{profile\}\"?\s*=="),
    re.compile(r"\$\{deployment\}\"?\s*=="),
    re.compile(r"\$\{mode\}\"?\s*=="),
    re.compile(r"BREV_ENV_ID"),
    re.compile(r"SKIP_HARDWARE_CHECK"),
)

BASH_OPENERS = ("if ", "elif ", "case ", "for ", "while ", "until ")
BASH_CLOSERS = ("fi", "esac", "done")
FUNCTION_START = re.compile(r"^(function\s+[A-Za-z0-9_]+|[A-Za-z0-9_]+\s*\(\))")


@dataclass(frozen=True)
class ProfileFamily:
    """A directory of profiles and the deploy script that reads them."""

    env_root: str
    script: str


PROFILE_FAMILIES = (
    ProfileFamily(
        env_root="deploy/docker/developer-profiles",
        script="deploy/docker/scripts/dev-profile.sh",
    ),
    ProfileFamily(
        env_root="deploy/docker/industry-profiles",
        script="deploy/docker/scripts/blueprint-deploy.sh",
    ),
)


def is_env_file(path: Path) -> bool:
    """Match env files including a bare ``.env``, whose ``Path.suffix`` is empty."""
    return path.suffix == ENV_SUFFIX or path.name == ENV_SUFFIX


def profile_env_files(root: Path) -> list[Path]:
    """A profile's own top-level env files. ``generated.env`` is a runtime
    artefact written by the deploy script and is not checked in."""
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if is_env_file(path)
        and len(path.relative_to(root).parts) == 2
        and path.name != "generated.env"
    )


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def device_assignments(path: Path) -> list[tuple[int, str, str]]:
    """``(line number, key, unquoted value)`` for every device-index key."""
    found: list[tuple[int, str, str]] = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ASSIGNMENT.match(line)
        if not match:
            continue
        name = match.group("name")
        if DEVICE_KEY_RE.match(name):
            found.append((line_number, name, strip_quotes(match.group("value"))))
    return found


def parse_device_list(value: str) -> tuple[list[int], list[str]]:
    """Split a committed value into ``(indices, unparsable entries)``.

    An entry containing ``$`` is neither: its index is only known at deploy
    time, so it is reported as unknown by returning it in neither list while
    still requiring its key to be clamped (see ``scan_family``)."""
    indices: list[int] = []
    invalid: list[str] = []
    for raw in value.replace(" ", "").split(","):
        if not raw or "$" in raw:
            continue
        if raw.isdigit():
            indices.append(int(raw))
        else:
            invalid.append(raw)
    return indices, invalid


def declared_clamp_keys(script_text: str) -> set[str]:
    """Keys enumerated in the deploy script's clamp arrays."""
    keys: set[str] = set()
    for array in CLAMP_KEY_ARRAYS:
        match = re.search(rf"^{array}=\((.*?)^\)", script_text, re.M | re.S)
        if not match:
            continue
        keys.update(re.findall(r"['\"]([A-Z0-9_]+)['\"]", match.group(1)))
    return keys


def is_single_line_block(stripped: str) -> bool:
    """``case x in y) z ;; esac`` on one line opens and closes in place, so it
    must not move the depth counter in ``enclosing_conditions``."""
    for opener, closer in (("if ", "fi"), ("case ", "esac"), ("for ", "done"), ("while ", "done")):
        if stripped.startswith(opener) and re.search(rf"(^|[;\s]){closer}\s*$", stripped):
            return True
    return False


def enclosing_conditions(lines: list[str], index: int) -> list[str]:
    """Conditions of the blocks that enclose ``lines[index]``, innermost first.

    Walks upward to the start of the enclosing function, counting closers so
    that sibling blocks already closed above the call are not mistaken for
    enclosing ones."""
    conditions: list[str] = []
    pending = 0
    # Set after recording an ``elif``: the earlier branches of that same chain,
    # and the ``if`` that heads it, are not conditions on this call. Reporting
    # them would put a branch the call cannot be in into the diagnostic.
    in_chain = False
    for cursor in range(index - 1, -1, -1):
        stripped = lines[cursor].strip()
        if not stripped or stripped.startswith("#"):
            continue
        if FUNCTION_START.match(lines[cursor]):
            break
        if is_single_line_block(stripped):
            continue
        if stripped in BASH_CLOSERS or stripped.rstrip(";") in BASH_CLOSERS:
            pending += 1
            continue
        if stripped.startswith(BASH_OPENERS):
            if pending:
                pending -= 1
            elif in_chain:
                in_chain = stripped.startswith("elif ")
            else:
                conditions.append(stripped)
                in_chain = stripped.startswith("elif ")
    return conditions


def clamp_call_lines(lines: list[str]) -> list[int]:
    """Indices of lines that call the clamp, excluding its own definition."""
    calls: list[int] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if CLAMP_FUNCTION not in stripped or stripped.startswith("#"):
            continue
        if re.match(rf"^(function\s+)?{CLAMP_FUNCTION}\s*\(\)", stripped):
            continue
        calls.append(index)
    return calls


def scan_family(family: ProfileFamily, report: list[str]) -> list[str]:
    """Diagnostics for one profile directory and its deploy script."""
    failures: list[str] = []
    script_path = ROOT / family.script
    if not script_path.is_file():
        return [f"{family.script}: deploy script is missing; nothing clamps {family.env_root}"]

    script_text = script_path.read_text()
    script_lines = script_text.splitlines()
    declared = declared_clamp_keys(script_text)
    if not declared:
        failures.append(
            f"{family.script}: no {' or '.join(CLAMP_KEY_ARRAYS)} array found; "
            "the clamp cannot enumerate the keys it is supposed to handle"
        )

    # (3) The clamp must run, and must run for every profile on every host.
    calls = clamp_call_lines(script_lines)
    if CLAMP_FUNCTION not in script_text:
        failures.append(
            f"{family.script}: {CLAMP_FUNCTION} is not defined; committed device "
            "indices reach Compose unresolved and a host with fewer GPUs than the "
            "profile assumes fails to start"
        )
    elif not calls:
        failures.append(
            f"{family.script}: {CLAMP_FUNCTION} is defined but never called; a "
            "helper nothing invokes is why this defect shipped in the first place"
        )
    else:
        unscoped = []
        for call in calls:
            conditions = enclosing_conditions(script_lines, call)
            scoping = [
                condition
                for condition in conditions
                if any(pattern.search(condition) for pattern in SCOPING_CONDITIONS)
            ]
            if not scoping:
                unscoped.append(call)
        if not unscoped:
            call = calls[0]
            conditions = enclosing_conditions(script_lines, call)
            failures.append(
                f"{family.script}:{call + 1}: every {CLAMP_FUNCTION} call is gated on "
                f"a single profile or environment ({conditions!r}); the general case "
                "goes unclamped, which is exactly how get_nvidia_smi_gpu_count came "
                "to be called only from the BREV_ENV_ID search pre-flight"
            )

    # The clamp has to compare against the host, not a constant.
    if not any(name in script_text for name in GPU_COUNT_FUNCTIONS):
        failures.append(
            f"{family.script}: none of {GPU_COUNT_FUNCTIONS} is used; the clamp has "
            "no GPU count to clamp against"
        )

    # (1) and (2). A profile's placement is spread across .env and
    # overrides.env, so the GPU requirement is only meaningful per profile.
    env_root = ROOT / family.env_root
    for profile_dir in sorted({path.parent for path in profile_env_files(env_root)}):
        placement: dict[str, int] = {}
        for env_file in sorted(profile_env_files(env_root)):
            if env_file.parent != profile_dir:
                continue
            display = env_file.relative_to(ROOT)
            for line_number, key, value in device_assignments(env_file):
                indices, invalid = parse_device_list(value)
                for entry in invalid:
                    failures.append(
                        f"{display}:{line_number}: {key} entry {entry!r} is not a "
                        "device index; Compose forwards it to device_ids verbatim "
                        "and the container fails to start with an opaque driver "
                        "error"
                    )
                if indices:
                    placement[key] = max(indices)
                needs_clamp = any(index > 0 for index in indices) or "$" in value
                if needs_clamp and key not in declared:
                    failures.append(
                        f"{display}:{line_number}: {key}={value!r} places a service "
                        f"above device 0, but {key} is not listed in "
                        f"{' / '.join(CLAMP_KEY_ARRAYS)} in {family.script}, so the "
                        "single-GPU clamp leaves it alone and a one-GPU host cannot "
                        "start this profile"
                    )
        name = profile_dir.relative_to(env_root)
        if not placement:
            report.append(f"  {name}: no device placement committed")
            continue
        highest = max(placement.values())
        detail = ", ".join(f"{key}={index}" for key, index in sorted(placement.items()))
        report.append(f"  {name}: needs {highest + 1} GPU(s)  [{detail}]")

    return failures


def scan(report: list[str] | None = None) -> list[str]:
    failures: list[str] = []
    for family in PROFILE_FAMILIES:
        failures.extend(scan_family(family, report if report is not None else []))
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress the per-profile GPU requirement summary",
    )
    args = parser.parse_args(argv)

    report: list[str] = []
    failures = scan(report)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    if not args.quiet:
        print("Committed GPU device placement:")
        print("\n".join(report))
    print(f"GPU device clamp lint passed ({len(report)} profiles).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
