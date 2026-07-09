# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = (
    REPO_ROOT / "skills" / "vss-search-archive" / "scripts" / "manage_search_source.sh"
)


class ManageSearchSourceTests(unittest.TestCase):
    def run_shell(
        self,
        body: str,
        *,
        extra_env: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            fake_bin = Path(temporary) / "bin"
            fake_bin.mkdir()
            uv = fake_bin / "uv"
            uv.write_text("#!/usr/bin/env bash\nexit 0\n")
            uv.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "ACTION": "test",
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "VSS_SEARCH_ARCHIVE_SOURCE_ONLY": "true",
                }
            )
            if extra_env:
                env.update(extra_env)
            command = textwrap.dedent(
                f"""
                source {RUNNER!s}
                {body}
                """
            )
            return subprocess.run(
                ["bash", "-c", command],
                check=check,
                text=True,
                capture_output=True,
                cwd=REPO_ROOT,
                env=env,
            )

    def test_file_ingest_happy_path(self) -> None:
        with tempfile.NamedTemporaryFile() as video:
            video.write(b"video")
            video.flush()
            result = self.run_shell(
                """
                VSS_AGENT_URL=http://agent
                VST_FORWARD_URL=http://vst
                FILE_PATH="$TEST_VIDEO"
                FILENAME=clip.mp4
                curl() {
                  cat >/dev/null || true
                  case "$*" in
                    *OPTIONS*) return 0 ;;
                    */complete*) printf '{"sensor_id":"file-id","chunks_processed":1}' ;;
                    *'/api/v1/videos'*) printf '{"url":"http://vst/vst/api/v1/storage/file"}' ;;
                    *) return 1 ;;
                  esac
                }
                upload_chunk() { printf '{"sensorId":"file-id"}'; }
                wait_vst_present() { return 0; }
                do_file_ingest
                """,
                extra_env={"TEST_VIDEO": video.name},
            )
        self.assertIn("File ingestion complete: sensor=file-id chunks=1", result.stdout)

    def test_delete_path_identifiers_reject_dot_segments(self) -> None:
        result = self.run_shell(
            """
            ! validate_source_name .
            ! validate_source_name ..
            ! validate_video_id .
            ! validate_video_id ..
            printf 'guarded\n'
            """
        )

        self.assertEqual("guarded", result.stdout.strip())

    def test_invalid_upload_sensor_id_is_not_armed_for_rollback(self) -> None:
        with tempfile.NamedTemporaryFile() as video, tempfile.NamedTemporaryFile() as calls:
            video.write(b"video")
            video.flush()
            result = self.run_shell(
                """
                VSS_AGENT_URL=http://agent
                VST_FORWARD_URL=http://vst
                FILE_PATH="$TEST_VIDEO"
                FILENAME=clip.mp4
                curl() {
                  cat >/dev/null || true
                  printf '%s\n' "$*" >>"$CALL_LOG"
                  case "$*" in
                    *OPTIONS*) return 0 ;;
                    *'/api/v1/videos'*) printf '{"url":"http://vst/vst/api/v1/storage/file"}' ;;
                    *) return 1 ;;
                  esac
                }
                upload_chunk() { printf '{"sensorId":".."}'; }
                do_file_ingest
                """,
                extra_env={"TEST_VIDEO": video.name, "CALL_LOG": calls.name},
                check=False,
            )
            calls.seek(0)
            call_log = calls.read().decode()

        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("-X DELETE", call_log)
        self.assertIn("VIDEO_ID has an invalid format", result.stderr)

    def test_rtsp_ingest_happy_path(self) -> None:
        result = self.run_shell(
            """
            VSS_AGENT_URL=http://agent
            RTSP_URL=rtsp://camera/live
            SOURCE_NAME=loading-dock
            RTSP_PASSWORD=''
            vst_sensor_absent() { return 0; }
            curl() { cat >/dev/null || true; printf '{"status":"success"}'; }
            resolve_video_id_by_name() { printf 'rtsp-id'; }
            wait_rtsp_searchable() { return 0; }
            do_rtsp_ingest
            """
        )
        self.assertIn(
            "RTSP ingestion searchable: sensor=rtsp-id name=loading-dock", result.stdout
        )

    def test_rtsp_password_file_without_trailing_newline_is_accepted(self) -> None:
        with tempfile.NamedTemporaryFile() as password, tempfile.NamedTemporaryFile() as request:
            password.write(b"super-secret")
            password.flush()
            os.chmod(password.name, 0o600)
            result = self.run_shell(
                """
                VSS_AGENT_URL=http://agent
                RTSP_URL=rtsp://camera/live
                SOURCE_NAME=loading-dock
                RTSP_PASSWORD_FILE="$PASSWORD_FILE"
                vst_sensor_absent() { return 0; }
                curl() { cat >"$REQUEST_BODY"; printf '{"status":"success"}'; }
                resolve_video_id_by_name() { printf 'rtsp-id'; }
                wait_rtsp_searchable() { return 0; }
                do_rtsp_ingest
                """,
                extra_env={"PASSWORD_FILE": password.name, "REQUEST_BODY": request.name},
            )
            request.seek(0)
            request_body = request.read().decode()

        self.assertEqual(0, result.returncode)
        self.assertIn("super-secret", request_body)

    def test_rtsp_local_credential_failure_does_not_arm_rollback(self) -> None:
        with tempfile.NamedTemporaryFile() as calls:
            result = self.run_shell(
                """
                VSS_AGENT_URL=http://agent
                RTSP_URL=rtsp://camera/live
                SOURCE_NAME=loading-dock
                RTSP_PASSWORD_FILE=/definitely/missing/password
                vst_sensor_absent() { return 0; }
                curl() { printf '%s\n' "$*" >>"$CALL_LOG"; return 1; }
                do_rtsp_ingest
                """,
                extra_env={"CALL_LOG": calls.name},
                check=False,
            )
            calls.seek(0)
            call_log = calls.read().decode()

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", call_log)
        self.assertIn("RTSP_PASSWORD_FILE is not readable", result.stderr)

    def test_management_url_rejects_userinfo_without_echoing_secret(self) -> None:
        result = self.run_shell(
            """
            candidate='https://alice'
            candidate+=':not-a-real-credential'
            candidate+='@example.com'
            validate_http_url VSS_AGENT_URL "$candidate"
            """,
            check=False,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("must not contain userinfo", result.stderr)
        self.assertNotIn("not-a-real-credential", result.stderr)

    def test_management_url_rejects_non_http_scheme(self) -> None:
        result = self.run_shell(
            """
            validate_http_url ES_URL 'file:///etc/passwd'
            """,
            check=False,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("must use http:// or https://", result.stderr)

    def test_rtsp_ingest_rejects_an_existing_name_before_post(self) -> None:
        result = self.run_shell(
            """
            VSS_AGENT_URL=http://agent
            RTSP_URL=rtsp://camera/live
            SOURCE_NAME=loading-dock
            RTSP_PASSWORD=''
            vst_sensor_absent() { return 1; }
            curl() { printf 'unexpected POST\n'; return 1; }
            do_rtsp_ingest
            """,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("unexpected POST", result.stdout)
        self.assertIn("SOURCE_NAME is already registered", result.stderr)

    def test_docker_setup_uses_discovered_profile_ports(self) -> None:
        result = self.run_shell(
            """
            DEPLOYMENT=docker
            PROFILE=search
            uv() {
              printf '{"agent_url":"http://127.0.0.1:18000","vst_url":"http://127.0.0.1:30889","es_url":"http://127.0.0.1:19200","embed":"http://127.0.0.1:19200","behavior":"http://127.0.0.1:19201","raw":"http://127.0.0.1:19202"}'
            }
            wait_http() { return 0; }
            setup_access
            printf '%s|%s|%s|%s|%s|%s\n' \
              "$VSS_AGENT_URL" "$VST_URL" "$ES_URL" "$BEHAVIOR_ES_URL" "$RAW_ES_URL" "$VST_FORWARD_URL"
            """
        )
        self.assertEqual(
            "http://127.0.0.1:18000|http://127.0.0.1:30889|http://127.0.0.1:19200|http://127.0.0.1:19201|http://127.0.0.1:19202|http://127.0.0.1:30889",
            result.stdout.strip(),
        )

    def test_kubernetes_file_delete_preserves_live_rtvi_routing_path(self) -> None:
        with tempfile.NamedTemporaryFile() as calls:
            result = self.run_shell(
                """
                ACTION=file-delete
                DEPLOYMENT=kubernetes
                NAMESPACE=vss
                RELEASE=search
                VSS_AGENT_URL=https://agent.example
                VST_URL=https://vst.example
                ES_URL=https://embed.example
                BEHAVIOR_ES_URL=https://behavior.example
                RAW_ES_URL=https://raw.example
                kubectl() { return 0; }
                discover_runtime_es_urls() {
                  printf 'runtime-called\n' >>"$CALL_LOG"
                  printf '{"rtvi_cv":"http://haproxy-controller.haproxy:80/rtvi-cv"}'
                }
                ensure_kubernetes_service_endpoint() {
                  printf '%s|%s|%s|%s\n' "$1" "${!1}" "$2" "$3" >>"$CALL_LOG"
                  if [[ "$1" == RTVI_CV_URL ]]; then
                    printf -v "$1" 'http://127.0.0.1:19000/rtvi-cv'
                  fi
                }
                wait_http() { printf 'wait|%s\n' "$1" >>"$CALL_LOG"; }
                setup_access
                printf '%s\n' "$RTVI_CV_URL"
                cat "$CALL_LOG"
                """,
                extra_env={"CALL_LOG": calls.name},
            )

        self.assertEqual("http://127.0.0.1:19000/rtvi-cv", result.stdout.splitlines()[0])
        self.assertIn("runtime-called", result.stdout)
        self.assertIn(
            "RTVI_CV_URL|http://haproxy-controller.haproxy:80/rtvi-cv|RTVI-CV|/docs",
            result.stdout,
        )
        self.assertIn("wait|http://127.0.0.1:19000/rtvi-cv/docs", result.stdout)

    def test_delete_verification_uses_each_index_family_endpoint(self) -> None:
        with tempfile.NamedTemporaryFile() as calls:
            result = self.run_shell(
                """
                ES_URL=http://embed-es
                BEHAVIOR_ES_URL=http://behavior-es
                RAW_ES_URL=http://raw-es
                VIDEO_INDEX=video
                BEHAVIOR_INDEX=behavior
                RAW_INDEX=raw
                vst_sensor_absent() { return 0; }
                vst_file_storage_absent() { return 0; }
                vst_file_media_absent() { return 0; }
                es_count() { printf '%s|%s\n' "$1" "$2" >>"$CALL_LOG"; printf '0'; }
                deleted_state_is_clean video_file video-id source-name
                cat "$CALL_LOG"
                """,
                extra_env={"CALL_LOG": calls.name},
            )
        self.assertEqual(
            [
                "http://embed-es|video",
                "http://behavior-es|behavior",
                "http://raw-es|raw",
            ],
            result.stdout.strip().splitlines(),
        )

    def test_failed_rtsp_add_never_deletes_unowned_same_name_source(self) -> None:
        with tempfile.NamedTemporaryFile() as calls:
            result = self.run_shell(
                r"""
                VSS_AGENT_URL=http://agent
                RTSP_URL=rtsp://camera/live
                SOURCE_NAME=loading-dock
                RTSP_PASSWORD=''
                vst_sensor_absent() { return 0; }
                curl() {
                  if [[ "$*" == *'--data-binary @-'* ]]; then cat >/dev/null; fi
                  printf '%s\n' "$*" >>"$CALL_LOG"
                  case "$*" in
                    *-X\ POST*) printf '{"status":"failure","error":"lookup failed"}' ;;
                    *-X\ DELETE*) printf '{"status":"success","name":"loading-dock"}' ;;
                    *) return 1 ;;
                  esac
                }
                do_rtsp_ingest
                """,
                extra_env={"CALL_LOG": calls.name},
                check=False,
            )
            calls.seek(0)
            call_log = calls.read().decode()
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("/api/v1/rtsp-streams/delete/loading-dock", call_log)
        self.assertIn("ownership was not confirmed", result.stderr)

    def test_exact_history_cleanup_uses_discovered_index_families(self) -> None:
        with tempfile.NamedTemporaryFile() as calls:
            result = self.run_shell(
                """
                ES_URL=http://embed-es
                BEHAVIOR_ES_URL=http://behavior-es
                RAW_ES_URL=http://raw-es
                VIDEO_INDEX=video
                VIDEO_INDEX_WILDCARD='video-*'
                BEHAVIOR_INDEX=behavior
                BEHAVIOR_INDEX_WILDCARD='behavior-*'
                RAW_INDEX=raw
                RAW_INDEX_WILDCARD='raw-*'
                es_delete_exact() { printf '%s|%s|%s|%s\n' "$1" "$2" "$3" "$4" >>"$CALL_LOG"; }
                delete_indexed_history rtsp sensor-id loading-dock
                cat "$CALL_LOG"
                """,
                extra_env={"CALL_LOG": calls.name},
            )

        self.assertEqual(
            [
                "http://embed-es|video-*,-video|sensor.id.keyword|loading-dock",
                "http://behavior-es|behavior-*,-behavior|sensor.id.keyword|loading-dock",
                "http://raw-es|raw-*,-raw|sensorId.keyword|loading-dock",
            ],
            result.stdout.strip().splitlines(),
        )

    def test_file_rtvi_cleanup_uses_sensor_routing_header(self) -> None:
        with tempfile.NamedTemporaryFile() as calls:
            result = self.run_shell(
                """
                RTVI_CV_URL=http://rtvi-cv
                curl() { printf '%s\n' "$*" >>"$CALL_LOG"; printf '204'; }
                remove_file_from_rtvi_cv sensor-123 clip
                cat "$CALL_LOG"
                """,
                extra_env={"CALL_LOG": calls.name},
            )

        self.assertIn("x-stream-id: sensor-123", result.stdout)
        self.assertIn("/api/v1/stream/remove", result.stdout)

    def test_file_storage_reconciliation_uses_exact_encoded_timeline_range(self) -> None:
        with tempfile.NamedTemporaryFile() as calls:
            result = self.run_shell(
                """
                VST_URL=http://vst
                curl() {
                  printf '%s\n' "$*" >>"$CALL_LOG"
                  case "$*" in
                    *storage/timelines*)
                      printf '{"file-id":[{"startTime":"2025-01-01T00:00:00.000Z","endTime":"2025-01-01T00:00:05.000Z"}]}'
                      ;;
                    *storage/file/file-id*) printf '204' ;;
                    *) return 1 ;;
                  esac
                }
                delete_vst_file_storage file-id
                cat "$CALL_LOG"
                """,
                extra_env={"CALL_LOG": calls.name},
            )

        self.assertIn("/vst/api/v1/storage/file/file-id?", result.stdout)
        self.assertIn("startTime=2025-01-01T00%3A00%3A00.000Z", result.stdout)
        self.assertIn("endTime=2025-01-01T00%3A00%3A05.000Z", result.stdout)

    def test_complete_index_overrides_skip_deployment_discovery(self) -> None:
        result = self.run_shell(
            """
            VIDEO_INDEX=tenant-video
            VIDEO_INDEX_WILDCARD='tenant-video-live-*'
            BEHAVIOR_INDEX=tenant-behavior
            BEHAVIOR_INDEX_WILDCARD='tenant-behavior-live-*'
            RAW_INDEX=tenant-raw
            RAW_INDEX_WILDCARD='tenant-raw-live-*'
            uv() { printf 'deployment discovery must not run\n' >&2; return 99; }
            discover_indexes
            printf '%s|%s|%s|%s|%s|%s\n' \
              "$VIDEO_INDEX" "$VIDEO_INDEX_WILDCARD" \
              "$BEHAVIOR_INDEX" "$BEHAVIOR_INDEX_WILDCARD" \
              "$RAW_INDEX" "$RAW_INDEX_WILDCARD"
            """
        )
        self.assertEqual(
            "tenant-video|tenant-video-live-*|tenant-behavior|tenant-behavior-live-*|tenant-raw|tenant-raw-live-*",
            result.stdout.strip(),
        )
        self.assertNotIn("deployment discovery", result.stderr)

    def test_streaming_ingest_indexes_override_search_runtime_indexes(self) -> None:
        result = self.run_shell(
            """
            printf '%s' '{
              "runtime": {
                "video":"runtime-video","video_wildcard":"runtime-video-*",
                "behavior":"runtime-behavior","behavior_wildcard":"runtime-behavior-*",
                "raw":"runtime-raw","raw_wildcard":"runtime-raw-*"
              },
              "streaming": {
                "video":"cleanup-video","video_wildcard":"cleanup-video-*",
                "behavior":"cleanup-behavior","behavior_wildcard":"cleanup-behavior-*",
                "raw":"cleanup-raw","raw_wildcard":"cleanup-raw-*"
              }
            }' | select_index_contract
            """
        )
        self.assertEqual(
            {
                "video": "cleanup-video",
                "video_wildcard": "cleanup-video-*",
                "behavior": "cleanup-behavior",
                "behavior_wildcard": "cleanup-behavior-*",
                "raw": "cleanup-raw",
                "raw_wildcard": "cleanup-raw-*",
            },
            json.loads(result.stdout),
        )

    def test_file_delete_uses_file_guard_and_agent_route(self) -> None:
        result = self.run_shell(
            """
            VSS_AGENT_URL=http://agent
            RTVI_CV_URL=http://rtvi-cv
            VIDEO_ID=file-id
            SOURCE_NAME=clip
            vst_source_pair_matches() { [[ "$1" == video_file && "$2" == file-id && "$3" == clip ]]; }
            curl() {
              [[ "$*" == *'/api/v1/videos/file-id'* ]] || return 1
              printf '{"status":"success","video_id":"file-id"}'
            }
            reconcile_file_delete_state() { [[ "$1" == file-id && "$2" == clip ]]; }
            wait_deleted_state() { [[ "$1" == video_file ]]; }
            do_file_delete
            """
        )
        self.assertIn("video_file deletion complete and verified", result.stdout)

    def test_file_delete_reconciles_and_verifies_agent_partial_status(self) -> None:
        with tempfile.NamedTemporaryFile() as calls:
            result = self.run_shell(
                """
                VSS_AGENT_URL=http://agent
                VIDEO_ID=file-id
                SOURCE_NAME=clip
                vst_source_pair_matches() { return 0; }
                curl() { printf '{"status":"partial","video_id":"file-id"}'; }
                reconcile_file_delete_state() {
                  printf 'reconcile|%s|%s\n' "$1" "$2" >>"$CALL_LOG"
                }
                wait_deleted_state() {
                  printf 'verify|%s|%s|%s\n' "$1" "$2" "$3" >>"$CALL_LOG"
                }
                do_file_delete
                cat "$CALL_LOG"
                """,
                extra_env={"CALL_LOG": calls.name},
            )

        self.assertIn("agent_status=partial", result.stdout)
        self.assertIn("reconcile|file-id|clip", result.stdout)
        self.assertIn("verify|video_file|file-id|clip", result.stdout)

    def test_file_delete_does_not_accept_partial_when_reconciliation_fails(self) -> None:
        result = self.run_shell(
            """
            VSS_AGENT_URL=http://agent
            VIDEO_ID=file-id
            SOURCE_NAME=clip
            vst_source_pair_matches() { return 0; }
            curl() { printf '{"status":"partial","video_id":"file-id"}'; }
            reconcile_file_delete_state() { return 1; }
            wait_deleted_state() { printf 'verification must not run\n'; return 0; }
            do_file_delete
            """,
            check=False,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("deletion complete", result.stdout)
        self.assertNotIn("verification must not run", result.stdout)

    def test_rtsp_delete_uses_rtsp_guard_and_agent_route(self) -> None:
        result = self.run_shell(
            """
            VSS_AGENT_URL=http://agent
            VIDEO_ID=rtsp-id
            SOURCE_NAME=loading-dock
            vst_source_pair_matches() { [[ "$1" == rtsp && "$2" == rtsp-id && "$3" == loading-dock ]]; }
            curl() {
              [[ "$*" == *'/api/v1/rtsp-streams/delete/loading-dock'* ]] || return 1
              printf '{"status":"success","name":"loading-dock"}'
            }
            delete_indexed_history() { [[ "$1" == rtsp && "$2" == rtsp-id && "$3" == loading-dock ]]; }
            wait_deleted_state() { [[ "$1" == rtsp ]]; }
            do_rtsp_delete
            """
        )
        self.assertIn("rtsp deletion complete and verified", result.stdout)

    def test_source_pair_requires_live_expected_kind(self) -> None:
        result = self.run_shell(
            """
            VST_URL=http://vst
            curl() {
              case "$*" in
                *sensor/list*) printf '[{"sensorId":"id","name":"camera","state":"online","type":"sensor_rtsp"}]' ;;
                *) return 1 ;;
              esac
            }
            vst_source_pair_matches rtsp id camera
            ! vst_source_pair_matches video_file id camera
            curl() { printf '[{"sensorId":"id","name":"camera","state":"removed","type":"sensor_rtsp"}]'; }
            ! vst_source_pair_matches rtsp id camera
            curl() { printf '[{"sensorId":"id","name":"camera","state":"online","type":"sensor_file"}]'; }
            vst_source_pair_matches video_file id camera
            ! vst_source_pair_matches rtsp id camera
            printf 'guarded\n'
            """
        )
        self.assertEqual("guarded", result.stdout.strip())

    def test_rtsp_readiness_failure_rolls_back(self) -> None:
        with tempfile.NamedTemporaryFile() as calls:
            result = self.run_shell(
                r"""
                VSS_AGENT_URL=http://agent
                RTSP_URL=rtsp://camera/live
            SOURCE_NAME=loading-dock
            RTSP_PASSWORD=''
            vst_sensor_absent() { return 0; }
                curl() {
                  if [[ "$*" == *'--data-binary @-'* ]]; then cat >/dev/null; fi
                  printf '%s\n' "$*" >>"$CALL_LOG"
                  case "$*" in
                    *-X\ DELETE*) printf '{"status":"success","name":"loading-dock"}' ;;
                    *) printf '{"status":"success"}' ;;
                  esac
                }
                resolve_video_id_by_name() { printf 'rtsp-id'; }
                resolve_rtsp_rollback_sensor() { printf 'rtsp-id'; }
                wait_rtsp_searchable() { return 1; }
                delete_indexed_history() { printf 'history|%s|%s|%s\n' "$1" "$2" "$3" >>"$CALL_LOG"; }
                wait_deleted_state() { printf 'verify|%s|%s|%s\n' "$1" "$2" "$3" >>"$CALL_LOG"; }
                do_rtsp_ingest
                """,
                extra_env={"CALL_LOG": calls.name},
                check=False,
            )
            calls.seek(0)
            call_log = calls.read().decode()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("/api/v1/rtsp-streams/delete/loading-dock", call_log)
        self.assertIn("history|rtsp|rtsp-id|loading-dock", call_log)
        self.assertIn("verify|rtsp|rtsp-id|loading-dock", call_log)
        self.assertIn("rolling back sensor=rtsp-id name=loading-dock", result.stderr)

    def test_rtsp_id_resolution_failure_refuses_unverified_name_delete(self) -> None:
        with tempfile.NamedTemporaryFile() as calls:
            result = self.run_shell(
                r"""
                VSS_AGENT_URL=http://agent
                RTSP_URL=rtsp://camera/live
                SOURCE_NAME=loading-dock
                RTSP_PASSWORD=''
                vst_sensor_absent() { return 0; }
                curl() {
                  if [[ "$*" == *'--data-binary @-'* ]]; then cat >/dev/null; fi
                  printf '%s\n' "$*" >>"$CALL_LOG"
                  case "$*" in
                    *-X\ DELETE*) printf '{"status":"success","name":"loading-dock"}' ;;
                    *) printf '{"status":"success"}' ;;
                  esac
                }
                resolve_video_id_by_name() { return 1; }
                resolve_rtsp_rollback_sensor() { return 1; }
                sleep() { SECONDS=$((SECONDS + 61)); }
                do_rtsp_ingest
                """,
                extra_env={"CALL_LOG": calls.name},
                check=False,
            )
            calls.seek(0)
            call_log = calls.read().decode()
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("/api/v1/rtsp-streams/delete/loading-dock", call_log)
        self.assertIn("no name-addressed delete was sent", result.stderr)

    def test_kubernetes_direct_rtvi_service_rejects_multiple_ready_backends(self) -> None:
        result = self.run_shell(
            r"""
            NAMESPACE=vss
            RELEASE=search
            RTVI_CV_URL=http://vss-rtvi-cv:9000
            uv() {
              while [[ "$1" != python ]]; do shift; done
              shift
              python3 "$@"
            }
            kubectl() {
              case "$*" in
                *'get service'*) printf '{"metadata":{"name":"vss-rtvi-cv"}}' ;;
                *'get endpoints'*)
                  printf '{"subsets":[{"addresses":[{"ip":"10.0.0.1"},{"ip":"10.0.0.2"}]}]}'
                  ;;
                *) return 1 ;;
              esac
            }
            start_service_forward() { printf 'unexpected-forward\n'; }
            ! ensure_kubernetes_service_endpoint RTVI_CV_URL RTVI-CV /docs
            printf 'guarded\n'
            """
        )
        self.assertEqual("guarded", result.stdout.strip())
        self.assertIn("has 2 ready backends", result.stderr)

    def test_kubernetes_rtvi_ingress_requires_live_hash_affinity_contract(self) -> None:
        result = self.run_shell(
            r"""
            NAMESPACE=vss
            RELEASE=search
            RTVI_CV_URL=http://haproxy-controller.haproxy:80/rtvi-cv
            uv() {
              while [[ "$1" != python ]]; do shift; done
              shift
              python3 "$@"
            }
            kubectl() {
              case "$*" in
                *'get service vss-rtvi-cv'*)
                  printf '{"metadata":{"name":"vss-rtvi-cv","labels":{"app.kubernetes.io/name":"vss-rtvi-cv","app.kubernetes.io/instance":"search"}}}'
                  ;;
                *'get service'*)
                  printf '{"metadata":{"name":"haproxy-controller","labels":{"app.kubernetes.io/name":"kubernetes-ingress"}}}'
                  ;;
                *'get ingress'*)
                  printf '%s' '{"items":[{"metadata":{"annotations":{"haproxy.org/load-balance":"hdr(x-stream-id)","haproxy.org/hash-type":"consistent"}},"spec":{"rules":[{"http":{"paths":[{"path":"/rtvi-cv","backend":{"service":{"name":"vss-rtvi-cv"}}}]}}]}}]}'
                  ;;
                *) return 1 ;;
              esac
            }
            start_service_forward() { printf -v "$5" 'http://127.0.0.1:19000/rtvi-cv'; }
            ensure_kubernetes_service_endpoint RTVI_CV_URL RTVI-CV /docs
            printf '%s|%s\n' "$RTVI_CV_404_SAFE" "$RTVI_CV_URL"
            """
        )
        self.assertEqual("true|http://127.0.0.1:19000/rtvi-cv", result.stdout.strip())

    def test_external_rtvi_path_does_not_imply_hash_affinity(self) -> None:
        result = self.run_shell(
            r"""
            NAMESPACE=vss
            RELEASE=search
            RTVI_CV_URL=https://cv.example/proxy
            uv() {
              while [[ "$1" != python ]]; do shift; done
              shift
              python3 "$@"
            }
            ensure_kubernetes_service_endpoint RTVI_CV_URL RTVI-CV /docs
            printf '%s\n' "$RTVI_CV_404_SAFE"
            """
        )
        self.assertEqual("false", result.stdout.strip())

    def test_rtvi_404_requires_verified_affinity_or_singleton_route(self) -> None:
        result = self.run_shell(
            """
            RTVI_CV_URL=http://rtvi-cv
            RTVI_CV_404_SAFE=false
            RTVI_BODY='{"detail":"Not Found"}'
            curl() {
              local output='' previous='' argument
              cat >/dev/null || true
              for argument in "$@"; do
                [[ "$previous" == -o ]] && output="$argument"
                previous="$argument"
              done
              printf '%s' "$RTVI_BODY" >"$output"
              printf '404'
            }
            ! remove_file_from_rtvi_cv sensor-123 clip
            RTVI_CV_404_SAFE=true
            ! remove_file_from_rtvi_cv sensor-123 clip
            RTVI_BODY='{"code":"NotFound","message":"No stream found with camera_id: sensor-123"}'
            remove_file_from_rtvi_cv sensor-123 clip
            printf 'guarded\n'
            """
        )
        self.assertEqual("guarded", result.stdout.strip())
        self.assertIn("unverified 404", result.stderr)

    def test_rtsp_rollback_waits_for_delayed_unique_sensor(self) -> None:
        with tempfile.NamedTemporaryFile() as counter:
            counter.write(b"0")
            counter.flush()
            result = self.run_shell(
                r"""
                VST_URL=http://vst
                RTSP_ROLLBACK_DISCOVERY_SECONDS=5
                curl() {
                  count="$(cat "$COUNTER")"
                  count=$((count + 1))
                  printf '%s' "$count" >"$COUNTER"
                  if ((count == 1)); then
                    printf '[]'
                  else
                    printf '[{"sensorId":"late-id","name":"loading-dock"}]'
                  fi
                }
                sleep() { SECONDS=$((SECONDS + 2)); }
                resolve_rtsp_rollback_sensor loading-dock ''
                """,
                extra_env={"COUNTER": counter.name},
            )
        self.assertEqual("late-id", result.stdout.strip())

    def test_rtsp_rollback_refuses_ambiguous_name(self) -> None:
        result = self.run_shell(
            r"""
            VST_URL=http://vst
            curl() {
              printf '[{"sensorId":"one","name":"loading-dock"},{"sensorId":"two","name":"loading-dock"}]'
            }
            ! resolve_rtsp_rollback_sensor loading-dock ''
            printf 'guarded\n'
            """
        )
        self.assertEqual("guarded", result.stdout.strip())
        self.assertIn("is ambiguous", result.stderr)

    def test_file_delete_verification_requires_empty_physical_file_list(self) -> None:
        result = self.run_shell(
            """
            vst_sensor_absent() { return 0; }
            vst_file_storage_absent() { return 0; }
            vst_file_media_absent() { return 1; }
            es_count() { printf 'unexpected-es-count\n'; return 0; }
            ! deleted_state_is_clean video_file file-id clip
            printf 'guarded\n'
            """
        )
        self.assertEqual("guarded", result.stdout.strip())
        self.assertNotIn("unexpected-es-count", result.stdout)

    def test_exact_delete_treats_missing_index_as_already_clean(self) -> None:
        with tempfile.NamedTemporaryFile() as calls:
            result = self.run_shell(
                """
                curl() { cat >/dev/null; printf '%s\n' "$*" >>"$CALL_LOG"; printf '{"timed_out":false,"failures":[]}'; }
                es_delete_exact http://es absent-index sensor.id.keyword file-id true
                cat "$CALL_LOG"
                """,
                extra_env={"CALL_LOG": calls.name},
            )
        self.assertIn("ignore_unavailable=true&allow_no_indices=true", result.stdout)

    def test_embed_index_cleanup_requires_the_concrete_contract_to_exist(self) -> None:
        with tempfile.NamedTemporaryFile() as calls:
            result = self.run_shell(
                """
                curl() { cat >/dev/null; printf '%s\n' "$*" >>"$CALL_LOG"; printf '{"timed_out":false,"failures":[]}'; }
                es_delete_exact http://es video-index sensor.id.keyword file-id false
                cat "$CALL_LOG"
                """,
                extra_env={"CALL_LOG": calls.name},
            )
        self.assertIn("ignore_unavailable=false&allow_no_indices=false", result.stdout)

    def test_index_cleanup_preserves_failure_when_later_families_succeed(self) -> None:
        result = self.run_shell(
            """
            ES_URL=http://embed
            BEHAVIOR_ES_URL=http://behavior
            RAW_ES_URL=http://raw
            VIDEO_INDEX=video
            BEHAVIOR_INDEX=behavior
            RAW_INDEX=raw
            calls=0
            es_delete_exact() {
              calls=$((calls + 1))
              printf '%s\n' "$2"
              ((calls != 1))
            }
            ! delete_indexed_history video_file file-id clip
            printf 'guarded\n'
            """
        )
        self.assertEqual(["video", "behavior", "raw", "guarded"], result.stdout.strip().splitlines())

    def test_file_completion_rejects_mismatched_sensor_identity(self) -> None:
        with tempfile.NamedTemporaryFile() as video:
            video.write(b"video")
            video.flush()
            result = self.run_shell(
                r"""
                VSS_AGENT_URL=http://agent
                VST_FORWARD_URL=http://vst
                FILE_PATH="$TEST_VIDEO"
                FILENAME=clip.mp4
                curl() {
                  cat >/dev/null || true
                  case "$*" in
                    *OPTIONS*) return 0 ;;
                    */complete*) printf '{"sensor_id":"other-id","chunks_processed":1}' ;;
                    *'-X DELETE'*) printf '{"status":"failure","video_id":"file-id"}' ;;
                    *'/api/v1/videos'*) printf '{"url":"http://vst/vst/api/v1/storage/file"}' ;;
                    *) return 1 ;;
                  esac
                }
                upload_chunk() { printf '{"sensorId":"file-id"}'; }
                do_file_ingest
                """,
                extra_env={"TEST_VIDEO": video.name},
                check=False,
            )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("invalid or mismatched sensor identity", result.stderr)

    def test_unknown_file_completion_outcome_is_never_certified(self) -> None:
        with tempfile.NamedTemporaryFile() as video:
            video.write(b"video")
            video.flush()
            result = self.run_shell(
                r"""
                VSS_AGENT_URL=http://agent
                VST_FORWARD_URL=http://vst
                FILE_PATH="$TEST_VIDEO"
                FILENAME=clip.mp4
                curl() {
                  cat >/dev/null || true
                  case "$*" in
                    *OPTIONS*) return 0 ;;
                    */complete*) return 28 ;;
                    *'-X DELETE'*) printf '{"status":"success","video_id":"file-id"}' ;;
                    *'/api/v1/videos'*) printf '{"url":"http://vst/vst/api/v1/storage/file"}' ;;
                    *) return 1 ;;
                  esac
                }
                upload_chunk() { printf '{"sensorId":"file-id"}'; }
                reconcile_file_delete_state() { printf 'unexpected-reconcile\n'; }
                do_file_ingest
                """,
                extra_env={"TEST_VIDEO": video.name},
                check=False,
            )
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("unexpected-reconcile", result.stdout)
        self.assertNotIn("rollback reconciled and verified", result.stderr)
        self.assertIn("server-side embedding may still be running", result.stderr)

    def test_exported_rtsp_password_is_removed_before_child_processes(self) -> None:
        result = self.run_shell(
            r"""
            VSS_AGENT_URL=http://agent
            RTSP_URL=rtsp://camera/live
            SOURCE_NAME=loading-dock
            vst_sensor_absent() { return 0; }
            curl() {
              cat >/dev/null || true
              printenv RTSP_PASSWORD >/dev/null 2>&1 && printf 'credential-leaked\n'
              printf '{"status":"success"}'
            }
            resolve_video_id_by_name() { printf 'rtsp-id'; }
            wait_rtsp_searchable() { return 0; }
            do_rtsp_ingest
            """,
            extra_env={"RTSP_PASSWORD": "super-secret"},
        )
        self.assertNotIn("credential-leaked", result.stdout)
        self.assertIn("RTSP ingestion searchable", result.stdout)

    def test_main_removes_exported_rtsp_secrets_before_discovery(self) -> None:
        result = self.run_shell(
            r"""
            check_child_env() {
              ! printenv RTSP_PASSWORD >/dev/null 2>&1
              ! printenv RTSP_URL >/dev/null 2>&1
              ! printenv CAPTURED_RTSP_PASSWORD >/dev/null 2>&1
            }
            setup_access() { check_child_env; }
            discover_indexes() { check_child_env; }
            do_rtsp_ingest() {
              check_child_env
              [[ "$CAPTURED_RTSP_PASSWORD" == super-secret ]]
              [[ "$CAPTURED_RTSP_URL" == 'rtsp://camera/live?token=hidden' ]]
            }
            main
            printf 'guarded\n'
            """,
            extra_env={
                "ACTION": "rtsp-ingest",
                "RTSP_PASSWORD": "super-secret",
                "RTSP_URL": "rtsp://camera/live?token=hidden",
            },
        )
        self.assertEqual("guarded", result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
