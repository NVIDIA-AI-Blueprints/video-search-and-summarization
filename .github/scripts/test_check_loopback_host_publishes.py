#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Self-test for check_loopback_host_publishes.py.

Runs before the lint in CI. A lint of this shape fails open in three ways -- by
missing the caller that dials the publish, by accepting a profile that widens
the bind, and by keeping either verdict after the publish it is measured
against stopped being narrow -- so each is pinned here, the first with the exact
text that shipped the defect.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_loopback_host_publishes import INFRA_COMPOSE  # noqa: E402
from check_loopback_host_publishes import main  # noqa: E402
from check_loopback_host_publishes import scan_paths  # noqa: E402
from check_loopback_host_publishes import scan_publishes  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def scan_text(text: str, suffix: str = ".yml") -> tuple[list[str], int]:
    """Scan *text* as a file, returning the lint's own verdict."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"sample{suffix}"
        path.write_text(text, encoding="utf-8")
        return scan_paths([path])


# --------------------------------------------------------------------------
# A caller that dials the publish
# --------------------------------------------------------------------------
print("rejects a caller that dials the publish")

# Verbatim from deploy/docker/industry-profiles/smartcities/compose.yml before
# the fix. The importer's readiness probe was refused, it exited 1, and the ITS
# dashboard was never imported -- with nothing else in the deployment failing.
SHIPPED_DEFECT = (
    "  kibana-init-container-alerts:\n"
    "    container_name: vss-kibana-init\n"
    "    environment:\n"
    "      KIBANA_URL: http://${HOST_IP}:${KIBANA_HOST_PORT:-5601}/kibana\n"
    "      ES_URL: http://${HOST_IP}:${ELASTICSEARCH_HOST_PORT:-9200}\n"
)

failures, checked = scan_text(SHIPPED_DEFECT)
check("the shipped smartcities ES_URL is rejected", bool(failures), f"({failures})")
check("...and was actually examined", checked == 1, f"(checked={checked})")
check(
    "...naming the loopback bind as the reason",
    any("bound to loopback" in f for f in failures),
    f"({failures})",
)
check(
    "...and does not also flag Kibana, which is not loopback-bound",
    len(failures) == 1,
    f"({failures})",
)

# The literal port is the same dependency written without the variable, and is
# what a copy-paste of the fix's inverse looks like.
failures, _ = scan_text("      ES_URL: http://${HOST_IP}:9200\n")
check(
    "a literal port behind a host address is rejected", bool(failures), f"({failures})"
)

failures, _ = scan_text("      ES_URL: http://$EXTERNAL_IP:9200\n")
check("EXTERNAL_IP is rejected too", bool(failures), f"({failures})")

failures, _ = scan_text("PHOENIX_COLLECTOR_ENDPOINT=http://${HOST_IP}:6006\n", ".env")
check("the Phoenix publish is guarded as well", bool(failures), f"({failures})")

failures, _ = scan_text('  curl -fsS "http://${HOST_IP}:9200/_cluster/health"\n', ".sh")
check("a shell caller is rejected", bool(failures), f"({failures})")


# --------------------------------------------------------------------------
# A profile that widens the bind
# --------------------------------------------------------------------------
print("rejects a profile that widens the bind")

for wide in ("0.0.0.0", "::", "*", ""):
    failures, checked = scan_text(f"ELASTICSEARCH_HOST_BIND={wide}\n", ".env")
    check(f"a profile binding {wide or '<empty>'!r} is rejected", bool(failures))
    check("...and was actually examined", checked == 1, f"(checked={checked})")

failures, _ = scan_text("      - PHOENIX_HOST_BIND=0.0.0.0\n")
check(
    "a Compose environment list entry is rejected too", bool(failures), f"({failures})"
)


# --------------------------------------------------------------------------
# The fix, and the shapes that were never the problem
# --------------------------------------------------------------------------
print("accepts the fix")

failures, checked = scan_text(
    "  kibana-init-container-alerts:\n"
    "    container_name: vss-kibana-init\n"
    "    command: bash /opt/mdx/init-scripts/kibana-import-dashboard.sh\n"
)
check("the fix -- no override at all -- is accepted", not failures, f"({failures})")

failures, _ = scan_text("      ES_URL: http://elasticsearch:9200\n")
check("the service name is accepted", not failures, f"({failures})")

# A loopback bind still serves a probe issued on the host itself, which is what
# the launchable's endpoint checks do.
failures, _ = scan_text('  ("Elasticsearch", f"http://localhost:9200/")\n', ".sh")
check("a localhost probe is accepted", not failures, f"({failures})")

failures, _ = scan_text("ELASTICSEARCH_HOST_BIND=127.0.0.1\n", ".env")
check("an explicit loopback bind is accepted", not failures, f"({failures})")

# The publish itself names both the bind and the port on one line; reading it as
# a caller would make the lint fail on the very shape it requires.
failures, _ = scan_text(
    "      - ${ELASTICSEARCH_HOST_BIND:-127.0.0.1}:${ELASTICSEARCH_HOST_PORT:-9200}:9200\n"
)
check("the publish line itself is not a finding", not failures, f"({failures})")

# A profile stating the port is how every profile in this tree is written, and
# says nothing about who reaches it.
failures, _ = scan_text("ELASTICSEARCH_HOST_PORT=9200\n", ".env")
check("a bare port definition is not a finding", not failures, f"({failures})")

# Kafka publishes wide and advertises a host address on purpose; only the
# guarded services' ports are a finding.
failures, _ = scan_text(
    "      KAFKA_ADVERTISED_LISTENERS: EXTERNAL://${HOST_IP}:${KAFKA_HOST_PORT:-9092}\n"
)
check(
    "an unguarded service's host address is not a finding",
    not failures,
    f"({failures})",
)


# --------------------------------------------------------------------------
# scan_publishes: the premise the rest of the lint rests on
# --------------------------------------------------------------------------
print("scan_publishes requires the publishes to stay narrow")


def infra_tree(body: str) -> Path:
    """A throwaway tree holding the infra Compose file with *body*."""
    tmp = Path(tempfile.mkdtemp())
    path = tmp / INFRA_COMPOSE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return tmp


FIXED_PUBLISHES = (
    "  phoenix:\n"
    "    ports:\n"
    "    - ${PHOENIX_HOST_BIND:-127.0.0.1}:${PHOENIX_HOST_PORT:-6006}:6006\n"
    "  elasticsearch:\n"
    "    ports:\n"
    "      - ${ELASTICSEARCH_HOST_BIND:-127.0.0.1}:${ELASTICSEARCH_HOST_PORT:-9200}:9200\n"
)

check(
    "both loopback publishes are accepted",
    not scan_publishes(infra_tree(FIXED_PUBLISHES)),
)

# The pre-fix shape: bind folded into the port variable, which every profile
# sets to a bare port -- so the publish went wide in every real deployment.
check(
    "a publish with no bind of its own is a finding",
    bool(
        scan_publishes(
            infra_tree(
                FIXED_PUBLISHES.replace(
                    "${ELASTICSEARCH_HOST_BIND:-127.0.0.1}:${ELASTICSEARCH_HOST_PORT:-9200}",
                    "${ELASTICSEARCH_HOST_PORT:-9200}",
                )
            )
        )
    ),
)

check(
    "a bind defaulting wide is a finding",
    bool(
        scan_publishes(
            infra_tree(
                FIXED_PUBLISHES.replace(
                    "${PHOENIX_HOST_BIND:-127.0.0.1}", "${PHOENIX_HOST_BIND:-0.0.0.0}"
                )
            )
        )
    ),
)

check(
    "a guarded service that stopped publishing is a finding, not a skip",
    bool(
        scan_publishes(
            infra_tree(
                "".join(
                    line + "\n"
                    for line in FIXED_PUBLISHES.splitlines()
                    if "PHOENIX_HOST_PORT" not in line
                )
            )
        )
    ),
)

check(
    "a missing infra Compose file is a finding, not a skip",
    bool(scan_publishes(Path(tempfile.mkdtemp()))),
)


# --------------------------------------------------------------------------
# Non-vacuity: nothing to check is a failure, not a pass
# --------------------------------------------------------------------------
print("refuses to pass vacuously")

with tempfile.TemporaryDirectory() as tmp:
    empty = Path(tmp) / "nothing.yml"
    empty.write_text("      SOMETHING_ELSE: 1\n", encoding="utf-8")
    failures, checked = scan_paths([empty])
    check("a file with no reference yields no findings", not failures)
    check("...and nothing counted", checked == 0, f"(checked={checked})")

# The repository's own files must satisfy the lint, and it must find them.
check("the repository passes its own lint", main([]) == 0)


print()
if FAILURES:
    print(f"{len(FAILURES)} self-test failure(s): {', '.join(FAILURES)}")
    raise SystemExit(1)
print("check_loopback_host_publishes self-tests passed.")
