# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Post-deploy checks against the running active-learning stack.

Read-only: nothing here writes to the stack, so it is safe to rerun at any time.
"""

from __future__ import annotations

import json
import re

import pytest
from conftest import http_get, is_long_running, replicas, run

pytestmark = pytest.mark.live

# (service, container port, path, text the response body must contain)
HTTP_CHECKS = [
    ("al-archiver", 8000, "/health", '"ok"'),
    ("aoi-client", 8000, "/health", '"ok"'),
    ("aoi-inference", 5000, "/v1/health", '"ok"'),
    # backend-api and data-flywheel-api expose no health route; a served
    # OpenAPI document shows the app started and is routing requests.
    ("backend-api", 8000, "/openapi.json", '"paths"'),
    ("data-flywheel-api", 8000, "/openapi.json", '"paths"'),
    ("data-flywheel-model-manager", 8090, "/v1/health", '"ok"'),
    ("deft-service", 8000, "/v1/health", '"ok"'),
    ("kibana", 5601, "/api/status", '"level"'),
    ("logstash", 9600, "/", '"status"'),
    ("mlflow", 5000, "/health", "OK"),
    ("seaweedfs-master1", 9333, "/cluster/status", '"Leader"'),
    ("seaweedfs-s3", 8333, "/healthz", ""),
]
# One line per topic in `kafka-topics.sh --describe` output.
TOPIC_RE = re.compile(r"^Topic: (?P<topic>\S+)\s.*?PartitionCount: (?P<partitions>\d+)", re.M)


def exec_in(containers_by_service, service: str, *cmd: str) -> str:
    container = containers_by_service[service][0].name
    result = run(["docker", "exec", container, *cmd], timeout=60)
    assert result.returncode == 0, f"{service}: `{' '.join(cmd)}` failed:\n{result.stderr}"
    return result.stdout


def mount_source(service: dict, target: str) -> str:
    return next(v["source"] for v in service.get("volumes", []) if v["target"] == target)


def test_long_running_services_have_all_replicas_running(services, containers_by_service):
    problems = []
    for name, svc in sorted(services.items()):
        if not is_long_running(svc):
            continue
        running = [c for c in containers_by_service.get(name, []) if c.status == "running"]
        if len(running) != replicas(svc):
            problems.append(f"{name}: {len(running)}/{replicas(svc)} running")
    assert not problems, "Services not fully running:\n" + "\n".join(problems)


def test_containers_run_the_configured_image(services, containers):
    wrong = [
        f"{c.name}: runs {c.image}, config says {services[c.service]['image']}"
        for c in containers
        if c.service in services and c.image != services[c.service]["image"]
    ]
    assert not wrong, "Outdated containers (rerun docker compose up -d):\n" + "\n".join(wrong)


def test_no_orphan_containers(services, containers):
    orphans = sorted(c.name for c in containers if c.service not in services)
    assert not orphans, f"Containers from services no longer in the config: {orphans}"


def test_health_checks_pass(containers):
    unhealthy = [f"{c.name}: {c.health}" for c in containers if c.health not in (None, "healthy")]
    assert not unhealthy, (
        "Health checks not passing ('starting' means the stack has not settled yet):\n"
        + "\n".join(unhealthy)
    )


def test_long_running_containers_never_restarted(services, containers):
    restarted = [
        f"{c.name}: {c.restart_count} restarts (see `docker logs {c.name}`)"
        for c in containers
        if c.service in services and is_long_running(services[c.service]) and c.restart_count
    ]
    assert not restarted, "Containers that crashed and restarted:\n" + "\n".join(restarted)


def test_setup_jobs_exited_successfully(services, containers):
    failed = [
        f"{c.name}: {c.status}, exit code {c.exit_code} (see `docker logs {c.name}`)"
        for c in containers
        if c.service in services
        and not is_long_running(services[c.service])
        and (c.status != "exited" or c.exit_code != 0)
    ]
    assert not failed, "Setup and wait-for jobs that did not finish cleanly:\n" + "\n".join(failed)


@pytest.mark.parametrize(
    ("service", "port", "path", "expected"), HTTP_CHECKS, ids=[c[0] for c in HTTP_CHECKS]
)
def test_http_endpoint_responds(published_url, service, port, path, expected):
    url = published_url(service, port, path)
    status, body = http_get(url)
    assert status == 200, f"GET {url} -> {status}: {body[:300]}"
    assert expected in body, f"GET {url} body lacks {expected!r}: {body[:300]}"


def test_elasticsearch_cluster_is_not_red(published_url):
    url = published_url("elasticsearch", 9200, "/_cluster/health")
    status, body = http_get(url)
    assert status == 200, f"GET {url} -> {status}: {body[:300]}"
    assert json.loads(body)["status"] in ("green", "yellow"), body


def test_kafka_topics_exist_with_expected_partitions(services, containers_by_service):
    env = services["kafka-aoi-topic-bootstrap"]["environment"]
    expected = {
        t["name"]: int(t.get("partitions") or env["DEFAULT_PARTITIONS"])
        for t in json.loads(env["KAFKA_TOPICS"])
    }
    described = exec_in(
        containers_by_service,
        "kafka",
        "/opt/kafka/bin/kafka-topics.sh",
        "--bootstrap-server",
        "localhost:9092",
        "--describe",
    )
    actual = {m["topic"]: int(m["partitions"]) for m in TOPIC_RE.finditer(described)}
    missing = sorted(set(expected) - set(actual))
    assert not missing, f"Kafka topics missing: {missing}"
    wrong = {t: f"has {actual[t]}, expected {n}" for t, n in expected.items() if actual[t] != n}
    assert not wrong, f"Kafka topics with wrong partition counts: {wrong}"


def test_s3_bucket_exists(services, containers_by_service):
    bucket = services["seaweedfs-bucket-bootstrap"]["environment"]["AWS_BUCKET"]
    listing = exec_in(
        containers_by_service,
        "seaweedfs-filer",
        "wget",
        "-qO-",
        "--header",
        "Accept: application/json",
        "http://localhost:8888/buckets/",
    )
    buckets = [entry["FullPath"].rsplit("/", 1)[-1] for entry in json.loads(listing)["Entries"]]
    assert bucket in buckets, f"S3 bucket {bucket!r} not found; buckets: {buckets}"


def test_elasticsearch_index_template_installed(services, published_url):
    bootstrap = services["elasticsearch-aoi-template-bootstrap"]
    name = re.search(r"_index_template/([\w.-]+)", " ".join(bootstrap["command"])).group(1)
    with open(mount_source(bootstrap, "/tmp/aoi-inspection-template.json")) as f:
        want_patterns = json.load(f)["index_patterns"]
    url = published_url("elasticsearch", 9200, f"/_index_template/{name}")
    status, body = http_get(url)
    assert status == 200, f"Index template {name!r} not installed: GET {url} -> {status}"
    installed = json.loads(body)["index_templates"][0]["index_template"]
    assert installed["index_patterns"] == want_patterns, installed["index_patterns"]


def test_logstash_pipelines_running(services, published_url):
    with open(mount_source(services["logstash"], "/usr/share/logstash/config/pipelines.yml")) as f:
        want = set(re.findall(r"pipeline\.id:\s*(\S+)", f.read()))
    url = published_url("logstash", 9600, "/_node/pipelines")
    status, body = http_get(url)
    assert status == 200, f"GET {url} -> {status}: {body[:300]}"
    running = set(json.loads(body)["pipelines"])
    assert want <= running, f"Logstash pipelines not running: {sorted(want - running)}"
