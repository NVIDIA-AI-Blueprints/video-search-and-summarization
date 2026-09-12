# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for headless streaming-caption composition guidance."""

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL = REPO_ROOT / "skills/vss-build-vision-agent/SKILL.md"
RT_VLM_OWNER = REPO_ROOT / "skills/vss-build-vision-agent/references/services/rt-vlm.md"
EVAL_SPEC = (
    REPO_ROOT
    / "skills/vss-build-vision-agent/eval/profile_in_1_streaming_dense_captions.json"
)
BASE_ENV = REPO_ROOT / "deploy/docker/developer-profiles/dev-profile-base/.env"
RT_VLM_COMPOSE = (
    REPO_ROOT / "deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml"
)
INFRA_COMPOSE = REPO_ROOT / "deploy/docker/services/infra/compose.yml"
LOGSTASH_PIPELINE = (
    REPO_ROOT
    / "deploy/docker/services/infra/elk/logstash/pipelines/kafka/mdx-lvs-logstash.conf"
)


def test_headless_rt_vlm_guidance_prunes_unneeded_frontends() -> None:
    """RT-VLM Q&A must not pull the interactive orchestration tier back in."""
    skill = " ".join(SKILL.read_text().split())
    owner = " ".join(RT_VLM_OWNER.read_text().split())

    assert "HAProxy ingress (`ingress.md`) stays only when" in skill
    assert "Serve Q&A directly from `/v1/chat/completions`" in owner
    assert "`vss-agent`, `vss-ui`, `phoenix`, and the LLM NIM" in owner
    assert "HAProxy does not route RT-VLM" in owner
    assert "create no `patches/` directory" in owner


def test_caption_bus_guidance_matches_compose_sources() -> None:
    """Inherited and built-in values should not become redundant overrides."""
    owner = RT_VLM_OWNER.read_text()

    assert "STREAM_TYPE=kafka" in BASE_ENV.read_text()
    assert (
        'MESSAGE_BUS_TOPIC: "${RTVI_VLM_MESSAGE_BUS_TOPIC:-mdx-vlm-captions}"'
        in RT_VLM_COMPOSE.read_text()
    )
    assert '{"name": "mdx-vlm-captions"}' in INFRA_COMPOSE.read_text()
    assert (
        'topics                   => ["mdx-vlm-captions"'
        in LOGSTASH_PIPELINE.read_text()
    )

    assert "keep the inherited `STREAM_TYPE=kafka`" in owner
    assert "do not set the legacy, unused `RTVI_VLM_KAFKA_TOPIC`" in owner
    assert "do not override `KAFKA_TOPICS`" in owner


def test_streaming_caption_eval_enforces_the_guidance() -> None:
    """The behavioral spec should grade the contract without stale variables."""
    expects = json.loads(EVAL_SPEC.read_text())["expects"]
    proposal = "\n".join([expects[0]["query"], *expects[0]["checks"]])
    build = "\n".join([expects[1]["query"], *expects[1]["checks"]])
    runtime = "\n".join([expects[3]["query"], *expects[3]["checks"]])

    assert "exact service-set churn" in proposal
    assert "served directly by RT-VLM's `/v1/chat/completions`" in proposal
    assert "prunes `vss-haproxy-ingress`" in proposal
    assert "no `KAFKA_TOPICS` override" in proposal
    assert "legacy, unused `RTVI_VLM_KAFKA_TOPIC`" in proposal
    assert (
        "`STREAM_TYPE`, `RTVI_VLM_MESSAGE_BUS_TOPIC`, "
        "`RTVI_VLM_KAFKA_BOOTSTRAP_SERVERS`, and `KAFKA_TOPICS` are not set" in build
    )
    assert "six added and five removed service keys" in runtime
