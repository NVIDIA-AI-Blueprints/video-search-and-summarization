# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise actual HAProxy rules against a local dummy Elasticsearch backend.

Run after `docker pull haproxy:3.4.2-alpine`; skips if Docker/image is unavailable.
No deployment endpoints or credentials are used.
"""

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

IMAGE = "haproxy:3.4.2-alpine"


@pytest.fixture(scope="module")
def edge(tmp_path_factory):
    try:
        subprocess.run(
            ["docker", "image", "inspect", IMAGE],
            check=True,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("HAProxy test image unavailable")
    template = (
        Path(__file__).parents[1] / "services/infra/haproxy/haproxy.cfg.template"
    ).read_text()
    rules = template[
        template.index("    acl p_elasticsearch path ") : template.index(
            "    # RT-Embed, mounted"
        )
    ]
    config = tmp_path_factory.mktemp("edge") / "haproxy.cfg"
    config.write_text(
        "global\n    maxconn 64\ndefaults\n    mode http\n    timeout connect 2s\n    timeout client 5s\n    timeout server 5s\nfrontend main\n    bind :8080\n    acl h_main always_true\n"
        + rules
        + '\nbackend bk_elasticsearch_strip\n    http-request return status 200 content-type application/json string "{}"\n'
    )
    # Container runs unprivileged; pytest temp parents need not be traversable
    # because Docker mounts the file itself.
    config.chmod(0o644)
    run = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "-p",
            "127.0.0.1::8080",
            "-v",
            f"{config}:/usr/local/etc/haproxy/haproxy.cfg:ro",
            IMAGE,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    handle = run.stdout.strip()
    try:
        inspected = json.loads(
            subprocess.check_output(["docker", "inspect", handle], text=True)
        )[0]
        port = inspected["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostPort"]
        origin = f"http://127.0.0.1:{port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(50):
            try:
                with opener.open(origin + "/elasticsearch/_search", timeout=1):
                    break
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        else:
            pytest.fail("Isolated HAProxy fixture did not start")
        yield origin
    finally:
        subprocess.run(
            ["docker", "rm", "-f", handle], capture_output=True, timeout=15, check=False
        )


@pytest.mark.parametrize(
    "method,path,status",
    [
        ("DELETE", "/elasticsearch/_search/scroll", 200),
        ("DELETE", "/elasticsearch/_search/scroll?pretty=true", 200),
        ("POST", "/elasticsearch/_search/scroll", 200),
        ("PUT", "/elasticsearch/_search/scroll", 405),
        ("DELETE", "/elasticsearch/_search/scroll/_all", 405),
        ("DELETE", "/elasticsearch/mdx-behavior-2025-01-01", 405),
        ("DELETE", "/elasticsearch/vss-memory/_doc/job", 405),
        ("PUT", "/elasticsearch/vss-memory/_doc/job", 200),
        ("PUT", "/elasticsearch/vss-search/_doc/job", 405),
        ("GET", "/elasticsearch/_cluster/health", 403),
        ("POST", "/elasticsearch/mdx-raw-2025-01-01/_delete_by_query", 403),
        ("POST", "/elasticsearch/mdx-raw-2025-01-01/_search", 200),
    ],
)
def test_scroll_exception_preserves_other_restrictions(edge, method, path, status):
    request = urllib.request.Request(edge + path, method=method)
    # Ignore developer proxy settings for this isolated loopback harness.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=5) as response:
            actual = response.status
    except urllib.error.HTTPError as error:
        actual = error.code
    assert actual == status
