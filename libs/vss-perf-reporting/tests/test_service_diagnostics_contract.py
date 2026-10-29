# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
LVS = REPO_ROOT / "services/video-summarization/perf/scripts"
BA = REPO_ROOT / "services/analytics/behavior-analytics/perf/scripts"
VA_API = REPO_ROOT / "services/analytics/video-analytics-api/perf/scripts"


def test_each_service_collects_diagnostics_before_teardown() -> None:
    teardown_markers = {
        LVS: 'docker compose "${COMPOSE_FILE_ARGS[@]}" down',
        BA: '"$PERF_DIR/scripts/teardown.sh"',
        VA_API: '"$PERF_DIR/scripts/teardown.sh"',
    }
    for scripts, teardown_marker in teardown_markers.items():
        runner = (scripts / "run_benchmark.sh").read_text(encoding="utf-8")
        assert 'collect_perf_diagnostics "$exit_code"' in runner
        assert runner.index('collect_perf_diagnostics "$exit_code"') < runner.index(
            teardown_marker
        )
        assert "trap 'exit 130' INT" in runner
        assert "trap 'exit 143' TERM" in runner


def test_lvs_discovers_every_compose_container() -> None:
    runner = (LVS / "run_benchmark.sh").read_text(encoding="utf-8")
    assert 'docker compose "${COMPOSE_FILE_ARGS[@]}" ps -aq' in runner
    assert 'collector_args+=(--container "$container_id")' in runner
    assert runner.index('collect_perf_diagnostics "$exit_code"') < runner.index(
        'docker compose "${COMPOSE_FILE_ARGS[@]}" down'
    )


def test_analytics_services_expose_project_and_auxiliary_containers() -> None:
    ba_common = (BA / "common.sh").read_text(encoding="utf-8")
    assert '--compose-project "$COMPOSE_PROJECT_NAME"' in ba_common
    assert '--container "$MONITOR_NAME"' in ba_common

    va_common = (VA_API / "common.sh").read_text(encoding="utf-8")
    assert '--compose-project "$COMPOSE_PROJECT_NAME"' in va_common
    assert '--container "$APP_CONTAINER"' in va_common
    assert '--container "$ES_CONTAINER"' in va_common
    assert '--container "$ES_INIT_CONTAINER"' in va_common
