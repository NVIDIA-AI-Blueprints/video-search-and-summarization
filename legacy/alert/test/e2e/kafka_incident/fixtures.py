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

"""Fixtures and helpers for Kafka incident E2E tests."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, Optional, Sequence, Tuple

import pytest
import requests

# Public exports useful for tests
__all__ = [
    "ProcessHandle",
    "use_real_endpoints",
    "simulators",
    "kafka_config",
    "topic_initializer",
    "launch_process",
    "start_simulators",
]

def _env_flag(name: str, default: bool = False) -> bool:
    """Read an environment variable and coerce it to a boolean flag."""

    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


@pytest.fixture(scope="session")
def use_real_endpoints(pytestconfig: pytest.Config) -> bool:
    """Resolve whether the suite should talk to live services."""

    cli_requested = pytestconfig.getoption("use_real_endpoints")
    env_requested = _env_flag("ALERT_AGENT_USE_REAL_ENDPOINTS")
    return bool(cli_requested or env_requested)


def _make_subprocess(env: Optional[Dict[str, str]], *argv: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )


def _capture(proc: subprocess.Popen[str]) -> Iterator[str]:
    assert proc.stdout is not None
    for line in proc.stdout:
        yield line.rstrip()


def _wait_ready(check_fn: Callable[[], bool], timeout: float, interval: float = 0.5, name: str = "service") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check_fn():
            print(f"[{name}] ready")
            return
        time.sleep(interval)
    raise TimeoutError(f"{name} did not report healthy in time")


def _python_executable() -> str:
    return sys.executable or "python"


@dataclass
class ProcessHandle:
    name: str
    process: subprocess.Popen[str]

    def terminate(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


def _check_port(host: str, port: int, timeout: float = 0.5) -> bool:
    with socket.socket() as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            return True
        except OSError:
            return False


def _wait_http(url: str, name: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = requests.get(url, timeout=2)
            if response.status_code < 500:
                print(f"[{name}] HTTP ready: {url}")
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"{name} did not answer at {url}")


def launch_process(name: str, argv: Sequence[str], env: Optional[Dict[str, str]] = None) -> ProcessHandle:
    print(f"[{name}] starting: {' '.join(argv)}")
    proc = _make_subprocess(env or os.environ.copy(), *argv)
    print(f"[{name}] launched pid={proc.pid}")
    return ProcessHandle(name=name, process=proc)


def _wait_port(host: str, port: int, name: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _check_port(host, port):
            print(f"[{name}] TCP ready: {host}:{port}")
            return
        time.sleep(0.5)
    raise TimeoutError(f"{name} did not open {host}:{port}")


@pytest.fixture(scope="session")
def simulators(use_real_endpoints: bool) -> Tuple[ProcessHandle, ...]:
    if use_real_endpoints:
        return tuple()

    repo_root = Path(__file__).resolve().parents[3]
    processes: list[ProcessHandle] = []

    commands: Sequence[Tuple[str, Sequence[str], Callable[[], None]]] = [
        (
            "elastic",
            [_python_executable(), str(repo_root / "test/sim_scripts/elastic/elastic_sim.py")],
            lambda: _wait_http("http://127.0.0.1:9200/health", "elastic"),
        ),
        (
            "nim",
            [_python_executable(), str(repo_root / "test/sim_scripts/nim/nim_stub_server.py")],
            lambda: _wait_port("127.0.0.1", 18081, "nim"),
        ),
        (
            "vst",
            [_python_executable(), str(repo_root / "test/sim_scripts/vst/vst_sim.py")],
            lambda: _wait_http("http://127.0.0.1:30888/status", "vst"),
        ),
        (
            "vss",
            [_python_executable(), str(repo_root / "test/sim_scripts/vss/vss_sim.py")],
            lambda: _wait_http("http://127.0.0.1:8080/models", "vss"),
        ),
    ]

    for name, argv, readiness in commands:
        handle = launch_process(name, argv)
        processes.append(handle)
        try:
            readiness()
        except Exception:
            handle.terminate()
            raise

    def _all_healthy() -> bool:
        # naive readiness: ensure processes are still running
        return all(p.process.poll() is None for p in processes)

    try:
        _wait_ready(_all_healthy, timeout=5, name="simulators")
    except Exception:  # pragma: no cover - on failure, still clean up
        for handle in processes:
            handle.terminate()
        raise

    try:
        yield tuple(processes)
    finally:
        for handle in processes:
            handle.terminate()


@contextmanager
def start_simulators(use_real_endpoints: bool = False) -> Iterator[Tuple[ProcessHandle, ...]]:
    """Context manager to start/stop simulators for standalone use."""
    if use_real_endpoints:
        yield tuple()
        return

    repo_root = Path(__file__).resolve().parents[3]
    processes: list[ProcessHandle] = []

    commands: Sequence[Tuple[str, Sequence[str], Callable[[], None]]] = [
        (
            "elastic",
            [_python_executable(), str(repo_root / "test/sim_scripts/elastic/elastic_sim.py")],
            lambda: _wait_http("http://127.0.0.1:9200/health", "elastic"),
        ),
        (
            "nim",
            [_python_executable(), str(repo_root / "test/sim_scripts/nim/nim_stub_server.py")],
            lambda: _wait_port("127.0.0.1", 18081, "nim"),
        ),
        (
            "vst",
            [_python_executable(), str(repo_root / "test/sim_scripts/vst/vst_sim.py")],
            lambda: _wait_http("http://127.0.0.1:30888/status", "vst"),
        ),
        (
            "vss",
            [_python_executable(), str(repo_root / "test/sim_scripts/vss/vss_sim.py")],
            lambda: _wait_http("http://127.0.0.1:8080/models", "vss"),
        ),
    ]

    try:
        for name, argv, readiness in commands:
            handle = launch_process(name, argv)
            processes.append(handle)
            readiness()
        yield tuple(processes)
    finally:
        for handle in processes:
            handle.terminate()


@pytest.fixture(scope="session")
def kafka_config(use_real_endpoints: bool, kafka_service: ProcessHandle | None) -> Dict[str, str]:
    if use_real_endpoints:
        return {
            "bootstrap": os.environ.get("KAFKA_BOOTSTRAP", "127.0.0.1:9092"),
            "topic": os.environ.get("KAFKA_INCIDENT_TOPIC", "mdx-incidents"),
            "enhanced_topic": os.environ.get("KAFKA_ENHANCED_TOPIC", "alert-bridge-enhanced-alerts"),
            "incidents_topic": os.environ.get("KAFKA_INCIDENTS_TOPIC", "alert-bridge-incidents"),
        }

    config = {
        "bootstrap": "127.0.0.1:9092",
        "topic": "mdx-incidents",
        "enhanced_topic": "alert-bridge-enhanced-alerts",
        "incidents_topic": "alert-bridge-incidents",
    }

    return config


def _ensure_topics(bootstrap: str, topics: Iterable[str]) -> None:
    from confluent_kafka import KafkaException
    from confluent_kafka.admin import AdminClient, NewTopic

    admin = AdminClient({"bootstrap.servers": bootstrap})
    new_topics = [NewTopic(topic, num_partitions=1, replication_factor=1) for topic in topics]
    futures = admin.create_topics(new_topics)
    for topic, future in futures.items():
        try:
            future.result()
        except KafkaException as exc:  # topic may already exist
            error = exc.args[0]
            if getattr(error, "code", lambda: None)() != getattr(error, "TOPIC_ALREADY_EXISTS", None):
                raise


@pytest.fixture(scope="session")
def topic_initializer(kafka_config: Dict[str, str], use_real_endpoints: bool) -> None:
    if use_real_endpoints:
        return

    bootstrap = kafka_config["bootstrap"]
    topics = (
        kafka_config.get("topic", "mdx-incidents"),
        kafka_config.get("enhanced_topic", "alert-bridge-enhanced-alerts"),
        kafka_config.get("incidents_topic", "alert-bridge-incidents"),
    )
    try:
        _ensure_topics(bootstrap, topics)
    except ModuleNotFoundError:
        pytest.skip("confluent_kafka package not installed; skipping Kafka-dependent tests")
    except Exception as exc:
        pytest.skip(f"Kafka bootstrap {bootstrap} unavailable: {exc}")


@pytest.fixture(scope="session")
def kafka_service(use_real_endpoints: bool) -> Iterator[ProcessHandle | None]:
    """Start lightweight Kafka service for testing if not using real endpoints."""
    if use_real_endpoints:
        yield None
        return

    print("[kafka] checking for existing Kafka container...")
    
    # Check if Kafka is already running
    try:
        _wait_port("127.0.0.1", 9092, "kafka", timeout=5)
        print("[kafka] existing Kafka container is ready")
        yield None
        return
    except Exception:
        print("[kafka] no existing Kafka found, starting new container...")
    
    # Start Kafka container directly with Docker
    container_name = "alert-agent-kafka-test"
    
    # First, clean up any existing container
    subprocess.run([
        "docker", "rm", "-f", container_name
    ], capture_output=True)
    
    # Start new Kafka container
    start_cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "-p", "9092:9092",
        "-e", "KAFKA_BROKER_ID=1",
        "-e", "KAFKA_PROCESS_ROLES=broker,controller",
        "-e", "KAFKA_NODE_ID=1",
        "-e", "KAFKA_CONTROLLER_QUORUM_VOTERS=1@localhost:9093",
        "-e", "KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER",
        "-e", "KAFKA_LISTENERS=PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093",
        "-e", "KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092",
        "-e", "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT",
        "-e", "KAFKA_INTER_BROKER_LISTENER_NAME=PLAINTEXT",
        "-e", "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1",
        "-e", "CLUSTER_ID=MkU3OEVBNTcwNTJENDM2Qk",
        "confluentinc/cp-kafka:7.5.0"
    ]
    
    try:
        result = subprocess.run(start_cmd, capture_output=True, text=True, check=True)
        print(f"[kafka] started container: {result.stdout.strip()}")
        if result.stderr:
            print(f"[kafka] stderr: {result.stderr}")
    except subprocess.CalledProcessError as exc:
        pytest.skip(f"Failed to start Kafka container: {exc.stderr}")
    
    # Wait for Kafka to be ready
    print("[kafka] waiting for readiness...")
    try:
        _wait_port("127.0.0.1", 9092, "kafka", timeout=60)
        # Additional wait for Kafka to be fully initialized
        print("[kafka] waiting for full initialization...")
        time.sleep(10)
    except Exception as exc:
        # Clean up on failure
        subprocess.run([
            "docker", "rm", "-f", container_name
        ], capture_output=True)
        pytest.skip(f"Kafka failed to become ready: {exc}")
    
    print("[kafka] ready")
    
    try:
        yield None  # No process handle needed for Docker
    finally:
        print("[kafka] stopping container...")
        subprocess.run([
            "docker", "rm", "-f", container_name
        ], capture_output=True)
        print("[kafka] stopped")
