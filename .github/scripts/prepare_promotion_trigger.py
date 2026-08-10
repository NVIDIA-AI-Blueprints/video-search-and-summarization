#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Convert a passed GitLab test handoff into a promotion-only trigger."""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path


def promotion_variables(
    test_variables: dict[str, str],
    test_pipeline_id: str,
    *,
    agent_ui_config: str = "",
    alert_config: str = "",
    video_analytics_api_config: str = "",
    behavior_analytics_config: str = "",
    sdr_mw_l_config: str = "",
    vss_configurator_config: str = "",
    vss_rt_config_adaptor_config: str = "",
) -> dict[str, str]:
    required = {"VSS_RELEASE_SET_B64", "VSS_RELEASE_SET_ID", "VSS_PROMOTION_TAG"}
    missing = required - set(test_variables)
    if missing:
        raise ValueError(
            "test handoff is missing promotion variables: "
            + ", ".join(sorted(missing))
        )
    if not test_pipeline_id.isdigit():
        raise ValueError("test pipeline ID must be numeric")
    release_set = json.loads(
        base64.b64decode(test_variables["VSS_RELEASE_SET_B64"], validate=True)
    )
    built_names = {
        str(image.get("name") or "")
        for image in release_set.get("images", [])
        if image.get("strategy") == "build"
    }
    if built_names.intersection({"vss-agent", "vss-agent-ui"}) and not agent_ui_config:
        raise ValueError("agent/UI artifacts-promotion config path is required")
    if "vss-alert-ms" in built_names and not alert_config:
        raise ValueError("alert artifacts-promotion config path is required")
    if (
        "vss-video-analytics-api" in built_names
        and not video_analytics_api_config
    ):
        raise ValueError(
            "video analytics API artifacts-promotion config path is required"
        )
    if "vss-behavior-analytics" in built_names and not behavior_analytics_config:
        raise ValueError(
            "behavior analytics artifacts-promotion config path is required"
        )
    if "sdr-mw-l" in built_names and not sdr_mw_l_config:
        raise ValueError("sdr-mw-l artifacts-promotion config path is required")
    if "vss-configurator" in built_names and not vss_configurator_config:
        raise ValueError("vss-configurator artifacts-promotion config path is required")
    if "vss-rt-config-adaptor" in built_names and not vss_rt_config_adaptor_config:
        raise ValueError(
            "vss-rt-config-adaptor artifacts-promotion config path is required"
        )
    variables = dict(test_variables)
    variables["BUILD_TYPE"] = "ghcr-promotion"
    variables["VSS_TEST_PIPELINE_ID"] = test_pipeline_id
    if agent_ui_config:
        variables["AGENT_UI_ARTIFACTS_PROMOTION_CONFIG_PATH"] = agent_ui_config
    if alert_config:
        variables["ALERT_ARTIFACTS_PROMOTION_CONFIG_PATH"] = alert_config
    if video_analytics_api_config:
        variables["VIDEO_ANALYTICS_API_ARTIFACTS_PROMOTION_CONFIG_PATH"] = (
            video_analytics_api_config
        )
    if behavior_analytics_config:
        variables["BEHAVIOR_ANALYTICS_ARTIFACTS_PROMOTION_CONFIG_PATH"] = (
            behavior_analytics_config
        )
    if sdr_mw_l_config:
        variables["SDR_MW_L_ARTIFACTS_PROMOTION_CONFIG_PATH"] = sdr_mw_l_config
    if vss_configurator_config:
        variables["VSS_CONFIGURATOR_ARTIFACTS_PROMOTION_CONFIG_PATH"] = (
            vss_configurator_config
        )
    if vss_rt_config_adaptor_config:
        variables["VSS_RT_CONFIG_ADAPTOR_ARTIFACTS_PROMOTION_CONFIG_PATH"] = (
            vss_rt_config_adaptor_config
        )
    return variables


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-pipeline-id", required=True)
    parser.add_argument("--agent-ui-config", default="")
    parser.add_argument("--alert-config", default="")
    parser.add_argument("--video-analytics-api-config", default="")
    parser.add_argument("--behavior-analytics-config", default="")
    parser.add_argument("--sdr-mw-l-config", default="")
    parser.add_argument("--vss-configurator-config", default="")
    parser.add_argument("--vss-rt-config-adaptor-config", default="")
    args = parser.parse_args()
    raw = os.environ.get("DOWNSTREAM_EXTRA_VARIABLES_JSON", "").strip()
    github_env = os.environ.get("GITHUB_ENV", "").strip()
    if not raw or not github_env:
        raise SystemExit(
            "DOWNSTREAM_EXTRA_VARIABLES_JSON and GITHUB_ENV are required"
        )
    variables = promotion_variables(
        json.loads(raw),
        args.test_pipeline_id,
        agent_ui_config=args.agent_ui_config,
        alert_config=args.alert_config,
        video_analytics_api_config=args.video_analytics_api_config,
        behavior_analytics_config=args.behavior_analytics_config,
        sdr_mw_l_config=args.sdr_mw_l_config,
        vss_configurator_config=args.vss_configurator_config,
        vss_rt_config_adaptor_config=args.vss_rt_config_adaptor_config,
    )
    with Path(github_env).open("a") as output:
        output.write("DOWNSTREAM_EXTRA_VARIABLES_JSON<<EOF\n")
        output.write(json.dumps(variables, separators=(",", ":")) + "\n")
        output.write("EOF\n")
    print(
        "Prepared promotion-only trigger for tested pipeline "
        f"#{args.test_pipeline_id}."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"[promotion-trigger] ERROR {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
