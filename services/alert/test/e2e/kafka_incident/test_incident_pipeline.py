# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""End-to-end validation for incident processing via Kafka."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Dict, Tuple

import pytest
import requests

from . import fixtures


def _python_executable() -> str:
    return os.getenv("PYTEST_PYTHON", "python")


def _run_service(repo_root: Path, config_path: Path, use_docker: bool = False, docker_image: str = "alert-agent:base_container_upgrade") -> subprocess.Popen[str]:
    if use_docker:
        cmd = [
            "docker", "run", "--rm",
            "--network", "host",
            "-v", f"{config_path}:/app/config.yaml",
            docker_image,
        ]
    else:
        cmd = [
            _python_executable(),
            str(repo_root / "enhance_alert_with_vlm.py"),
            "--config",
            str(config_path),
        ]
    print(f"[service] starting: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    print(f"[service] launched pid={proc.pid}")
    return proc


def _wait_for_elastic(base_url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    url = f"{base_url.rstrip('/')}/health"
    while time.monotonic() < deadline:
        try:
            response = requests.get(url, timeout=2)
            if response.status_code == 200:
                return
        except requests.RequestException:
            time.sleep(1)
    raise TimeoutError(f"Elastic simulator at {url} did not report healthy")


def _collect_output(proc: subprocess.Popen[str]) -> str:
    if proc.stdout is None:
        return ""
    lines = proc.stdout.read()
    return lines


def _service_logs(proc: subprocess.Popen[str]) -> str:
    try:
        output = _collect_output(proc)
        if output:
            print(f"[service] logs:\n{output}")
        return output
    except Exception:
        return ""


@pytest.mark.usefixtures("topic_initializer", "kafka_service")
def test_kafka_incident_flow(
    kafka_config: Dict[str, str],
    simulators: Tuple[fixtures.ProcessHandle, ...],
    use_real_endpoints: bool,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    payload_path = repo_root / "test/protobuf/test_data/sample_incident.json"
    if use_real_endpoints:
        config_path = repo_root / "config.yaml"
    else:
        config_path = Path(__file__).resolve().parent / "config_sim.yaml"
        #config_path = repo_root / "config.yaml"
    elastic_url = os.environ.get("ELASTIC_URL", "http://127.0.0.1:9200")

    if not use_real_endpoints:
        _wait_for_elastic(elastic_url)

    # Check if we should use Docker container
    use_docker = os.environ.get("USE_DOCKER_CONTAINER", "").lower() in ("1", "true", "yes")
    docker_image = os.environ.get("DOCKER_IMAGE", "alert-agent:base_container_upgrade")
    service = _run_service(repo_root, config_path, use_docker=use_docker, docker_image=docker_image)
    try:
        producer_cmd = [
            _python_executable(),
            str(repo_root / "test/protobuf/produce_incident.py"),
            "--bootstrap",
            kafka_config["bootstrap"],
            "--topic",
            kafka_config["topic"],
            "--payload",
            str(payload_path),
            "--id-suffix",
            "pytest",
        ]
        try:
            result = subprocess.run(producer_cmd, capture_output=True, text=True, check=True)
            if result.stdout:
                print(f"[producer] stdout:\n{result.stdout}")
            if result.stderr:
                print(f"[producer] stderr:\n{result.stderr}")
        except subprocess.CalledProcessError as exc:
            logs = _service_logs(service)
            pytest.fail(f"Incident producer failed: {exc.stderr}\nService logs:\n{logs}")

        # Show service logs for a few seconds to see processing
        print("[service] showing logs for 10 seconds...")
        import threading
        def read_service_logs():
            try:
                for line in service.stdout:
                    print(f"[service] {line.rstrip()}")
            except Exception:
                pass
        
        log_thread = threading.Thread(target=read_service_logs, daemon=True)
        log_thread.start()
        time.sleep(10)

        # Check multiple possible index dates (today and incident timestamp date)
        possible_indices = [
            f"mdx-vlm-incidents-{time.strftime('%Y-%m-%d')}",
            "mdx-vlm-incidents-2025-09-26",  # Sample incident timestamp
        ]
        
        deadline = time.monotonic() + 60
        last_response: str | None = None
        while time.monotonic() < deadline:
            for index in possible_indices:
                url = f"{elastic_url}/{index}/_all"
                try:
                    response = requests.get(url, timeout=5)
                    last_response = response.text
                    if response.status_code == 200:
                        data = response.json()
                        documents = data.get("documents", [])
                        if documents:
                            first = documents[0].get("_source", {})
                            print(f"[elastic] document received from {index}:\n{first}")
                            # Verify sensorId is present (accept any valid sensor)
                            assert first.get("sensorId"), "sensorId should be present"
                            # Verify VLM response fields are present
                            info = first.get("info", {})
                            has_vlm = (
                                "reasoning" in info or 
                                "verdict" in info or 
                                "vlm_response" in info or 
                                "vlmResponse" in info
                            )
                            assert has_vlm, f"VLM response should be in info: {info}"
                            print(f"[test] SUCCESS: Document verified with verdict={info.get('verdict')}")
                            return
                except requests.RequestException:
                    pass
            time.sleep(2)

        logs = _service_logs(service)
        pytest.fail(
            "Timed out waiting for document in Elastic simulator\n"
            f"Checked indices: {possible_indices}\n"
            f"Last response: {last_response}\n"
            f"Service logs:\n{logs}"
        )
    finally:
        service.terminate()
        try:
            service.wait(timeout=10)
        except subprocess.TimeoutExpired:
            service.kill()
            service.wait()

