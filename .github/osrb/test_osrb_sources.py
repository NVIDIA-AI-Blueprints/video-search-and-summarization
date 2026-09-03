#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the manifest-less dependency inventory.

Half of these run against the repository's own Dockerfiles, compose files,
charts and workflows rather than fixtures. A fixture only proves the parser
handles the shape its author imagined; the shapes that actually broke earlier
attempts are in the tree — apt lists spread over 30 continuation lines, images
behind three levels of ``${VAR:-default}``, a Chart.yaml whose only
``dependencies`` entry is commented out, a compose service that carries both
``build:`` and ``image:``.

Run directly:

    python3 .github/osrb/test_osrb_sources.py
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MODULE_PATH = Path(__file__).with_name("osrb_sources.py")
MODULE_SPEC = importlib.util.spec_from_file_location("osrb_sources", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
osrb_sources = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(osrb_sources)

REPO_ROOT = Path(__file__).resolve().parents[2]


def packages(rows: list[dict[str, str]], language: str | None = None) -> set[str]:
    return {
        row["package"] for row in rows if language is None or row["language"] == language
    }


def versions(rows: list[dict[str, str]], package: str) -> set[str]:
    return {row["new_version"] for row in rows if row["package"] == package}


class ParseDockerfileTest(unittest.TestCase):
    def test_stage_alias_referenced_by_a_later_from_is_not_a_dependency(self) -> None:
        dockerfile = b"""
FROM alpine:3.24.1 AS jq-fetch
FROM confluentinc/cp-kafka:8.3.0 AS kafka-base
FROM kafka-base AS kafka-health-check
COPY --from=jq-fetch /jqbin/jq /usr/bin/jq
"""
        rows = osrb_sources.parse_dockerfile(dockerfile, "svc/Dockerfile")

        self.assertEqual(
            {"alpine", "confluentinc/cp-kafka"}, packages(rows, "container")
        )

    def test_copy_from_an_external_image_is_a_dependency(self) -> None:
        # ghcr.io/astral-sh/uv reaches the build this way and this way only —
        # it never appears in a FROM, so a FROM-only reader misses it entirely.
        dockerfile = b"""
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /usr/local/bin/uv
COPY --from=builder /app /app
COPY --from=0 /old /old
"""
        rows = osrb_sources.parse_dockerfile(dockerfile, "svc/Dockerfile")

        self.assertEqual({"python", "ghcr.io/astral-sh/uv"}, packages(rows, "container"))

    def test_scratch_is_not_an_image(self) -> None:
        rows = osrb_sources.parse_dockerfile(b"FROM scratch AS final\n", "s/Dockerfile")

        self.assertEqual([], rows)

    def test_from_resolves_arg_and_env_defaults(self) -> None:
        dockerfile = b"""
ARG PYTHON_VERSION=3.13
ARG DISTROLESS_TAG=${PYTHON_VERSION}-v4.0.6
ARG DISTROLESS_IMG=nvcr.io/nvidia/distroless/python
FROM $DISTROLESS_IMG:$DISTROLESS_TAG AS runtime
"""
        rows = osrb_sources.parse_dockerfile(dockerfile, "svc/Dockerfile")

        self.assertEqual(
            [("nvcr.io/nvidia/distroless/python", "3.13-v4.0.6")],
            [(row["package"], row["new_version"]) for row in rows],
        )

    def test_unresolvable_arch_stage_is_matched_as_a_stage_not_an_image(self) -> None:
        # TARGETARCH is injected by buildx, so `ocv-${TARGETARCH}` never
        # resolves here. It still names a stage, and reporting it would put a
        # package that does not exist in front of OSRB.
        dockerfile = b"""
ARG MANYLINUX_IMG=quay.io/pypa/manylinux_2_28
FROM ${MANYLINUX_IMG}_x86_64 AS ocv-amd64
FROM ${MANYLINUX_IMG}_aarch64 AS ocv-arm64
FROM ocv-${TARGETARCH} AS opencv-builder
"""
        rows = osrb_sources.parse_dockerfile(dockerfile, "svc/Dockerfile")

        self.assertEqual(
            {"quay.io/pypa/manylinux_2_28_x86_64", "quay.io/pypa/manylinux_2_28_aarch64"},
            packages(rows),
        )

    def test_digest_pin_keeps_the_tag_out_of_the_package_name(self) -> None:
        digest = "sha256:" + "a" * 64
        rows = osrb_sources.parse_dockerfile(
            f"FROM python:3.12-slim@{digest}\n".encode(), "svc/Dockerfile"
        )

        self.assertEqual("python", rows[0]["package"])
        self.assertEqual(f"3.12-slim@{digest}", rows[0]["new_version"])

    def test_apt_list_spread_over_continuations_is_read_whole(self) -> None:
        dockerfile = b"""
FROM ubuntu:24.04
RUN apt-get update && \\
    apt-get upgrade -y && \\
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \\
        libx264-dev \\
        # this one is commented out and must not be inventoried
        libgstreamer1.0-0 \\
        gstreamer1.0-plugins-ugly \\
    && rm -rf /var/lib/apt/lists/*
"""
        rows = osrb_sources.parse_dockerfile(dockerfile, "svc/Dockerfile")

        self.assertEqual(
            {"libx264-dev", "libgstreamer1.0-0", "gstreamer1.0-plugins-ugly"},
            packages(rows, "deb"),
        )
        # `rm`, `-rf` and the path must not leak in as packages.
        self.assertNotIn("rm", packages(rows))
        self.assertNotIn("/var/lib/apt/lists/*", packages(rows))

    def test_conditional_install_after_then_is_not_lost(self) -> None:
        # `then` sits where the command normally is. Reading it as the command
        # drops every arch-conditional install in the repo.
        dockerfile = b"""
FROM ubuntu:24.04
RUN if [ "$TARGETARCH" = "arm64" ]; then \\
        apt-get update && \\
            apt-get install -y libgdal-dev; \\
    fi
"""
        rows = osrb_sources.parse_dockerfile(dockerfile, "svc/Dockerfile")

        self.assertEqual({"libgdal-dev"}, packages(rows, "deb"))
        # The shell keyword closing the block is not a Debian package.
        self.assertNotIn("fi", packages(rows))

    def test_apk_and_dnf_versions_are_captured(self) -> None:
        dockerfile = b"""
FROM alpine:3.24.1
RUN apk add --no-cache curl=8.21.0-r0 bash
RUN dnf install -y libarchive
"""
        rows = osrb_sources.parse_dockerfile(dockerfile, "svc/Dockerfile")

        self.assertEqual({"curl", "bash"}, packages(rows, "apk"))
        self.assertEqual({"8.21.0-r0"}, versions(rows, "curl"))
        self.assertEqual({"libarchive"}, packages(rows, "rpm"))

    def test_package_list_hidden_behind_a_shell_substitution_invents_nothing(self) -> None:
        # `$(cat requirements_apt.txt)` cannot be resolved from this file. An
        # honest zero beats a package literally named "$(cat".
        dockerfile = b"""
FROM ubuntu:24.04
RUN apt-get update \\
    && apt-get install -y --no-install-recommends $(cat /tmp/requirements_apt.txt)
"""
        rows = osrb_sources.parse_dockerfile(dockerfile, "svc/Dockerfile")

        self.assertEqual(set(), packages(rows, "deb"))

    def test_pip_installs_are_inventoried_with_pins_and_flags_stripped(self) -> None:
        dockerfile = b"""
FROM python:3.13-slim
RUN pip3 install --default-timeout=100 --retries 20 --index-url https://pypi.org/simple \\
        transformers==4.57.6 "wheel>=0.46.2" numpy==1.26.4
RUN uv pip install --python-version 3.13 --target /tmp/packages -r -
RUN python3 -m pip install --no-cache-dir jinja2==3.1.4
RUN pip install --no-deps /tmp/wheels/local.whl . ./release[eval]
"""
        rows = osrb_sources.parse_dockerfile(dockerfile, "svc/Dockerfile")

        self.assertEqual(
            {"transformers", "wheel", "numpy", "jinja2"}, packages(rows, "python")
        )
        self.assertEqual({"4.57.6"}, versions(rows, "transformers"))
        # `wheel>=0.46.2` floats; claiming 0.46.2 shipped would be a guess.
        self.assertEqual({""}, versions(rows, "wheel"))
        # `--retries 20` and `--index-url <url>` are flag values, not packages.
        self.assertNotIn("20", packages(rows))
        self.assertNotIn("https:", packages(rows))

    def test_pip_version_from_a_build_arg_is_resolved(self) -> None:
        dockerfile = b"""
FROM python:3.13-slim
ARG TORCH_VERSION=2.10.0+cpu
RUN pip install --no-cache-dir "torch==${TORCH_VERSION}"
"""
        rows = osrb_sources.parse_dockerfile(dockerfile, "svc/Dockerfile")

        self.assertEqual({"2.10.0+cpu"}, versions(rows, "torch"))

    def test_pip_vcs_requirement_keeps_the_upstream_url(self) -> None:
        dockerfile = (
            b"FROM python:3.13-slim\n"
            b'RUN uv pip install "vss-ctx-rag @ '
            b'git+https://github.com/NVIDIA/context-aware-rag.git@3.1.0"\n'
        )
        rows = osrb_sources.parse_dockerfile(dockerfile, "svc/Dockerfile")

        self.assertEqual(1, len(packages(rows, "python")))
        row = next(row for row in rows if row["language"] == "python")
        self.assertEqual("vss-ctx-rag", row["package"])
        self.assertEqual("3.1.0", row["new_version"])
        self.assertIn("github.com/NVIDIA/context-aware-rag", row["repository_url"])

    def test_cloned_and_fetched_upstream_source_is_inventoried(self) -> None:
        dockerfile = b"""
FROM ubuntu:24.04
RUN git clone --depth 1 --branch v9.1.0 https://github.com/NVIDIA/DeepStream.git /tmp/ds
RUN git init /tmp/apps \\
    && git -C /tmp/apps remote add origin https://github.com/NVIDIA-AI-IOT/deepstream_python_apps.git
RUN curl -fsSL -o /usr/share/source/numpy.tar.gz \\
        https://github.com/numpy/numpy/archive/refs/tags/v2.2.6.tar.gz
RUN wget -O /patches/libssl3.deb https://security.debian.org/pool/libssl3_3.0.20_amd64.deb
"""
        rows = osrb_sources.parse_dockerfile(dockerfile, "svc/Dockerfile")

        self.assertEqual(
            {
                "NVIDIA/DeepStream",
                "NVIDIA-AI-IOT/deepstream_python_apps",
                "numpy/numpy",
                "libssl3_3.0.20_amd64.deb",
            },
            packages(rows, "source"),
        )
        self.assertEqual({"v9.1.0"}, versions(rows, "NVIDIA/DeepStream"))
        self.assertEqual({"v2.2.6"}, versions(rows, "numpy/numpy"))

    def test_fetching_a_licence_pdf_or_signing_key_is_not_a_dependency(self) -> None:
        dockerfile = b"""
FROM ubuntu:24.04
RUN curl -fL -o NVIDIA-Software-License-Agreement.pdf \\
        https://www.nvidia.com/content/licence.pdf
RUN curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /k.gpg
RUN curl -fsSL https://apt.envoyproxy.io/signing.key | gpg --dearmor -o /e.gpg
"""
        rows = osrb_sources.parse_dockerfile(dockerfile, "svc/Dockerfile")

        self.assertEqual(set(), packages(rows, "source"))

    def test_a_quoted_command_that_never_runs_is_not_an_install(self) -> None:
        # video-summarization writes an install line into a wrapper script with
        # printf. It is data, not a build step.
        dockerfile = (
            b"FROM ubuntu:24.04\n"
            b"RUN printf '%s\\n' "
            b"'    exec /usr/local/bin/uv pip install --system requests' > /run.sh\n"
        )
        rows = osrb_sources.parse_dockerfile(dockerfile, "svc/Dockerfile")

        self.assertEqual(set(), packages(rows, "python"))

    def test_rows_cite_the_line_and_the_owning_module(self) -> None:
        rows = osrb_sources.parse_dockerfile(
            b"# comment\nFROM alpine:3.24.1\n", "services/alert/Dockerfile"
        )

        self.assertEqual("services/alert/Dockerfile#L2", rows[0]["source_file"])
        self.assertEqual("services/alert", rows[0]["module"])
        self.assertEqual("container", rows[0]["source_kind"])

    def test_no_license_is_invented_for_a_container_dependency(self) -> None:
        # There is no offline map from `libx264-dev` to an SPDX id that is
        # right often enough to be trusted. A blank starts a review; a wrong
        # permissive guess ends one.
        rows = osrb_sources.parse_dockerfile(
            b"FROM alpine:3.24.1\nRUN apk add --no-cache libx264\n", "svc/Dockerfile"
        )

        self.assertTrue(all(row["new_license"] == "" for row in rows))
        self.assertTrue(all(row["risk"] == "Unknown" for row in rows))


class ParseComposeTest(unittest.TestCase):
    def test_service_images_are_inventoried_and_tags_stay_out_of_the_name(self) -> None:
        compose = b"""
services:
  grafana:
    image: docker.io/grafana/grafana:13.1.1-ubuntu
  kibana:
    image: 'docker.elastic.co/kibana/kibana:9.4.4'
  kafka:
    image: confluentinc/cp-kafka:8.3.0   # comment must not join the tag
"""
        rows = osrb_sources.parse_compose(compose, "deploy/docker/compose.yml")

        self.assertEqual(
            {
                ("docker.io/grafana/grafana", "13.1.1-ubuntu"),
                ("docker.elastic.co/kibana/kibana", "9.4.4"),
                ("confluentinc/cp-kafka", "8.3.0"),
            },
            {(row["package"], row["new_version"]) for row in rows},
        )
        self.assertTrue(all(row["source_kind"] == "compose" for row in rows))

    def test_a_service_that_builds_its_image_is_not_pulling_a_dependency(self) -> None:
        # Compose treats `image:` on a service with `build:` as the tag to give
        # the locally built image. `image: elasticsearch` here is built from
        # elasticsearch.Dockerfile — attributing it to Docker Hub would report
        # the wrong provenance and the wrong licence.
        compose = b"""
services:
  elasticsearch:
    build:
      context: .
      dockerfile: Dockerfiles/elasticsearch.Dockerfile
    image: elasticsearch
  redis:
    image: redis:8.10.0-alpine
"""
        rows = osrb_sources.parse_compose(compose, "deploy/docker/compose.yml")

        self.assertEqual({"redis"}, packages(rows))

    def test_vss_artifacts_this_repo_publishes_are_not_third_party(self) -> None:
        # The agent line is one YAML line in deploy/docker/services/agent/compose.yml,
        # split here only to stay inside the line limit.
        compose = (
            b"services:\n"
            b"  agent:\n"
            b"    image: ${VSS_AGENT_IMAGE:-${VSS_CONTAINER_REGISTRY:-"
            b"ghcr.io/nvidia-ai-blueprints/vss}/vss-agent}"
            b":${VSS_CONTAINER_TAG:-develop-latest}\n"
            b"  alert:\n"
            b"    image: nvcr.io/nvidia/vss-core/vss-alert-verification:3.2.0\n"
            b"  lvs:\n"
            b"    image: via-engine-${USER:-user}\n"
            b"  kafka:\n"
            b"    image: ${KAFKA_IMAGE:-confluentinc/cp-kafka:8.2.0}\n"
        )
        rows = osrb_sources.parse_compose(compose, "deploy/docker/compose.yml")

        self.assertEqual(
            [("confluentinc/cp-kafka", "8.2.0")],
            [(row["package"], row["new_version"]) for row in rows],
        )

    def test_an_image_with_no_resolvable_default_is_reported_not_dropped(self) -> None:
        # Dropping it is the silent blindness this module exists to end: the
        # reviewer must be told there is an image here they have to chase.
        compose = b"""
services:
  postgres:
    image: ${POSTGRES_IMAGE}
"""
        rows = osrb_sources.parse_compose(compose, "deploy/docker/compose.yml")

        self.assertEqual(1, len(rows))
        self.assertEqual("${POSTGRES_IMAGE}", rows[0]["package"])
        self.assertIn("unresolved", rows[0]["notes"])
        self.assertEqual("Unknown", rows[0]["risk"])

    def test_commented_images_and_keys_outside_services_are_ignored(self) -> None:
        compose = b"""
x-shared: &shared
  image: should-not-count:1

services:
  alert:
    image: nvcr.io/example/alert:3.2.0
    #image: alert-bridge:19
    environment:
      SOME_IMAGE: other/thing:2
volumes:
  data:
"""
        rows = osrb_sources.parse_compose(compose, "deploy/docker/compose.yml")

        self.assertEqual({"nvcr.io/example/alert"}, packages(rows))


class ParseHelmChartTest(unittest.TestCase):
    def test_chart_without_a_dependencies_block_is_zero_rows_not_a_crash(self) -> None:
        chart = b"""
apiVersion: v2
name: vss-rtvi-cv
description: A chart
type: application
version: 3.3.0
appVersion: "1.0"
maintainers: []
"""
        self.assertEqual([], osrb_sources.parse_helm_chart(chart, "deploy/helm/Chart.yaml"))

    def test_both_indentation_styles_and_a_commented_dependency(self) -> None:
        chart = b"""
apiVersion: v2
name: umbrella
dependencies:
  - name: infra
    version: 3.3.0
    repository: "file://../../services/infra"
    condition: infra.enabled
  # - name: kubernetes-ingress
  #   version: "1.49.0"
  #   repository: "https://haproxytech.github.io/helm-charts"
  - name: kubernetes-ingress
    version: "1.49.0"
    repository: "https://haproxytech.github.io/helm-charts"
maintainers: []
"""
        rows = osrb_sources.parse_helm_chart(chart, "deploy/helm/Chart.yaml")

        self.assertEqual(
            [("infra", "3.3.0"), ("kubernetes-ingress", "1.49.0")],
            [(row["package"], row["new_version"]) for row in rows],
        )
        self.assertEqual(
            "https://haproxytech.github.io/helm-charts", rows[1]["repository_url"]
        )
        self.assertIn("in-repo subchart", rows[0]["notes"])
        self.assertNotIn("in-repo subchart", rows[1]["notes"])
        self.assertEqual("chart", rows[0]["source_kind"])

    def test_flush_left_list_style_terminates_at_the_next_top_level_key(self) -> None:
        chart = b"""
apiVersion: v2
dependencies:
- name: vios
  version: 3.3.0
  repository: file://../../../services/vios
maintainers:
- name: NVIDIA
"""
        rows = osrb_sources.parse_helm_chart(chart, "deploy/helm/Chart.yaml")

        # `maintainers: - name: NVIDIA` must not become a chart dependency.
        self.assertEqual([("vios", "3.3.0")], [(r["package"], r["new_version"]) for r in rows])


class ParseCMakeTest(unittest.TestCase):
    def test_all_four_declaration_forms(self) -> None:
        cmake = b"""
cmake_minimum_required(VERSION 3.20)
# find_package(CommentedOut REQUIRED)
find_package(Threads REQUIRED)
find_package(OpenCV 4.11 REQUIRED COMPONENTS core imgproc)
pkg_check_modules(GST REQUIRED IMPORTED_TARGET gstreamer-1.0>=1.20 gstreamer-app-1.0)
FetchContent_Declare(
  googletest
  GIT_REPOSITORY https://github.com/google/googletest.git
  GIT_TAG        v1.14.0
)
ExternalProject_Add(x264
  URL https://code.videolan.org/videolan/x264/-/archive/master/x264-master.tar.gz
)
"""
        rows = osrb_sources.parse_cmake(cmake, "services/x/CMakeLists.txt")
        by_name = {row["package"]: row for row in rows}

        self.assertEqual(
            {"OpenCV", "gstreamer-1.0", "gstreamer-app-1.0", "googletest", "x264"},
            set(by_name),
        )
        self.assertEqual("4.11", by_name["OpenCV"]["new_version"])
        self.assertEqual("1.20", by_name["gstreamer-1.0"]["new_version"])
        self.assertEqual("v1.14.0", by_name["googletest"]["new_version"])
        self.assertEqual(
            "https://github.com/google/googletest.git",
            by_name["googletest"]["repository_url"],
        )
        self.assertTrue(all(row["source_kind"] == "build" for row in rows))
        self.assertNotIn("CommentedOut", by_name)
        # REQUIRED / IMPORTED_TARGET are keywords, not native libraries.
        self.assertNotIn("REQUIRED", by_name)
        self.assertNotIn("IMPORTED_TARGET", by_name)
        # CMake's own pthread shim is not a shippable component.
        self.assertNotIn("Threads", by_name)


class ParsePreCommitTest(unittest.TestCase):
    def test_local_and_meta_repos_are_not_third_party(self) -> None:
        config = b"""
default_install_hook_types: [pre-commit, commit-msg]

repos:
- repo: https://github.com/trufflesecurity/trufflehog
  rev: v3.94.2
  hooks:
  - id: trufflehog
    name: TruffleHog secret scan
- repo: meta
  hooks:
  - id: check-useless-excludes
- repo: local
  hooks:
  - id: ruff-check
    name: ruff check (agent)
"""
        rows = osrb_sources.parse_precommit(config, ".pre-commit-config.yaml")

        self.assertEqual(
            [("trufflesecurity/trufflehog", "v3.94.2")],
            [(row["package"], row["new_version"]) for row in rows],
        )
        self.assertEqual("ci", rows[0]["source_kind"])
        self.assertIn("does not ship", rows[0]["notes"])

    def test_the_repositorys_own_config_is_read(self) -> None:
        config = REPO_ROOT / ".pre-commit-config.yaml"

        rows = osrb_sources.parse_precommit(config.read_bytes(), ".pre-commit-config.yaml")

        self.assertIn("trufflesecurity/trufflehog", packages(rows))
        self.assertNotIn("local", packages(rows))


class ParseActionsWorkflowTest(unittest.TestCase):
    def test_sha_pin_is_the_version_and_the_tag_comment_is_kept(self) -> None:
        workflow = b"""
jobs:
  build:
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5  # v4.3.1
      - uses: ./.github/actions/local-thing
      - uses: NVIDIA/skills/.github/workflows/team-request.yml@main
      - run: echo not-a-uses
"""
        rows = osrb_sources.parse_actions_workflow(workflow, ".github/workflows/ci.yml")
        by_name = {row["package"]: row for row in rows}

        self.assertEqual({"actions/checkout", "NVIDIA/skills"}, set(by_name))
        checkout = by_name["actions/checkout"]
        self.assertEqual("34e114876b0b11c390a56381ad16ebd13914f8d5", checkout["new_version"])
        self.assertIn("v4.3.1", checkout["notes"])
        self.assertEqual("https://github.com/actions/checkout", checkout["repository_url"])
        self.assertEqual("main", by_name["NVIDIA/skills"]["new_version"])
        self.assertIn("reusable workflow", by_name["NVIDIA/skills"]["notes"])
        self.assertEqual("ci", checkout["source_kind"])


class ParserRoutingTest(unittest.TestCase):
    def test_paths_are_routed_to_the_parser_that_can_read_them(self) -> None:
        cases = {
            "services/alert/Dockerfile": "parse_dockerfile",
            "services/vios/cicd_files/x86_64/Dockerfile.base": "parse_dockerfile",
            "deploy/docker/services/infra/Dockerfiles/elasticsearch.Dockerfile": "parse_dockerfile",
            "deploy/docker/compose.yml": "parse_compose",
            "services/alert/deploy_docker-compose.yml": "parse_compose",
            "deploy/helm/services/agent/Chart.yaml": "parse_helm_chart",
            "deploy/helm/services/agent/Chart.lock": "parse_helm_chart",
            "services/x/CMakeLists.txt": "parse_cmake",
            "services/x/deps.cmake": "parse_cmake",
            ".pre-commit-config.yaml": "parse_precommit",
            ".github/workflows/ci.yml": "parse_actions_workflow",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                routed = osrb_sources.parser_for(path)
                self.assertIsNotNone(routed)
                self.assertEqual(expected, routed[1].__name__)

    def test_lookalikes_are_not_routed(self) -> None:
        for path in (
            "services/agent/Dockerfile.dockerignore",
            "services/ui/package.json",
            "docs/compose.md",
            "deploy/docker/test-scripts/compose-images.golden",
        ):
            with self.subTest(path=path):
                self.assertIsNone(osrb_sources.parser_for(path))


class InventoryAtRefTest(unittest.TestCase):
    def test_missing_blob_is_normal_and_duplicates_collapse_within_a_module(self) -> None:
        """Same image, same module, two Dockerfiles -> one row. Missing blob -> skipped.

        This originally asserted that `a/Dockerfile` and `b/Dockerfile` collapse
        to a single row. They do not, and should not: they are different
        modules, and OSRB approves per component. The old expectation was the
        bug -- it dropped 87 of 450 rows on the real tree. Collapsing is correct
        only WITHIN one module, which is what this now checks.
        """
        blobs = {
            "services/a/docker/Dockerfile": b"FROM alpine:3.24.1\n",
            "services/a/docker/build.Dockerfile": b"FROM alpine:3.24.1\n",
        }

        def read(_ref: str, path: str) -> bytes | None:
            return blobs.get(path)

        inventory = osrb_sources.inventory_at_ref(
            "HEAD",
            read,
            [
                "services/a/docker/Dockerfile",
                "services/a/docker/build.Dockerfile",
                "services/a/docker/missing.Dockerfile",
            ],
        )

        self.assertEqual(1, len(inventory))
        row = next(iter(inventory.values()))
        # Sorted order makes the surviving citation deterministic.
        self.assertEqual("services/a/docker/Dockerfile#L1", row["source_file"])

    def test_the_same_image_in_two_modules_stays_two_rows(self) -> None:
        """The counterpart: two components pulling one image are two approvals."""
        blobs = {
            "services/a/Dockerfile": b"FROM alpine:3.24.1\n",
            "services/b/Dockerfile": b"FROM alpine:3.24.1\n",
        }

        def read(_ref: str, path: str) -> bytes | None:
            return blobs.get(path)

        inventory = osrb_sources.inventory_at_ref(
            "HEAD", read, ["services/a/Dockerfile", "services/b/Dockerfile"]
        )

        self.assertEqual(2, len(inventory))
        self.assertEqual(
            {"services/a", "services/b"},
            {row["module"] for row in inventory.values()},
        )

    def test_a_parser_that_raises_produces_an_uncovered_row_not_an_exception(self) -> None:
        # One malformed file must not take the gate down for every other PR in
        # the repo — but it must not vanish either.
        def explode(_data: bytes, _path: str) -> list[dict[str, str]]:
            raise ValueError("unreadable")

        original = osrb_sources.parse_dockerfile
        osrb_sources.parse_dockerfile = explode
        try:
            inventory = osrb_sources.inventory_at_ref(
                "HEAD", lambda _ref, _path: b"FROM alpine:3\n", ["svc/Dockerfile"]
            )
        finally:
            osrb_sources.parse_dockerfile = original

        self.assertEqual(1, len(inventory))
        row = next(iter(inventory.values()))
        self.assertEqual("UNCOVERED_SOURCE", row["change"])
        self.assertEqual("svc/Dockerfile", row["source_file"])
        self.assertIn("unreadable", row["notes"])


class AgentAContractTest(unittest.TestCase):
    def test_every_file_osrb_scan_calls_parsed_by_sources_has_a_parser(self) -> None:
        """The two coverage lists must agree, or the gate goes quiet again.

        `osrb_scan.is_parsed` promises OSRB that a container/compose/chart/
        build/ci file's dependencies were inventoried. If `parser_for` returns
        None for one of those, nothing raises and nothing is reported — the
        file simply contributes no rows, which is the exact failure mode this
        change exists to remove.
        """
        import osrb_scan

        checked = 0
        for path in sorted(REPO_ROOT.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(REPO_ROOT).as_posix()
            if relative.startswith(".git/"):
                continue
            kind = osrb_scan.is_dependency_file(relative)
            if kind not in osrb_scan._KINDS_PARSED_BY_SOURCES:
                continue
            if not osrb_scan.is_parsed(relative):
                continue
            checked += 1
            routed = osrb_sources.parser_for(relative)
            self.assertIsNotNone(routed, f"osrb_scan calls {relative} parsed; parser_for skips it")
            self.assertEqual(kind, routed[0], f"source_kind disagreement on {relative}")
        self.assertGreater(checked, 100)


class RealRepositoryTreeTest(unittest.TestCase):
    """Every dependency-bearing file in this repo, through its real parser.

    A fixture cannot prove the gate survives contact with 44 Dockerfiles, 65
    compose files, 48 charts and 20 workflows. A single raise here is a
    silently shrinking inventory in production.
    """

    def _files(self, parser_name: str) -> list[Path]:
        found: list[Path] = []
        for path in sorted(REPO_ROOT.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(REPO_ROOT).as_posix()
            if "node_modules/" in relative or relative.startswith(".git/"):
                continue
            routed = osrb_sources.parser_for(relative)
            if routed is not None and routed[1].__name__ == parser_name:
                found.append(path)
        return found

    def _parse_all(self, parser_name: str) -> dict[str, list[dict[str, str]]]:
        parser = getattr(osrb_sources, parser_name)
        results: dict[str, list[dict[str, str]]] = {}
        for path in self._files(parser_name):
            relative = path.relative_to(REPO_ROOT).as_posix()
            try:
                results[relative] = parser(path.read_bytes(), relative)
            except Exception as exc:  # noqa: BLE001 - the assertion is the point
                self.fail(f"{parser_name} raised on {relative}: {exc!r}")
        return results

    def test_every_chart_parses_and_the_ones_without_dependencies_are_empty(self) -> None:
        results = self._parse_all("parse_helm_chart")

        # Non-vacuity, not a census. _parse_all already fails on any file that
        # raises, so what is left to guard is discovery silently returning
        # nothing. Pinning a count instead turns an ordinary change -- adding
        # or removing a service -- into a test failure that says nothing true
        # about the parser.
        self.assertTrue(results, "no Chart.yaml discovered")
        without = [
            relative
            for relative, rows in results.items()
            if not any(
                line.startswith("dependencies:")
                for line in (REPO_ROOT / relative).read_text().splitlines()
            )
        ]
        self.assertTrue(without, "no dependency-free chart discovered")
        for relative in without:
            self.assertEqual([], results[relative], f"{relative} invented dependencies")
        for relative, rows in results.items():
            if relative not in without:
                self.assertTrue(rows, f"{relative} declares dependencies but produced none")

    def test_every_dockerfile_parses_and_the_known_third_parties_are_found(self) -> None:
        results = self._parse_all("parse_dockerfile")

        self.assertTrue(results, "no Dockerfile discovered")
        found = {row["package"] for rows in results.values() for row in rows}
        for expected in (
            "docker.elastic.co/elasticsearch/elasticsearch",
            "confluentinc/cp-kafka",
            "ghcr.io/astral-sh/uv",
            "ffmpeg",
            "gstreamer1.0-plugins-ugly",
        ):
            self.assertIn(expected, found)

    def test_every_compose_file_parses_and_the_audited_images_are_found(self) -> None:
        results = self._parse_all("parse_compose")

        self.assertTrue(results, "no compose file discovered")
        found = {row["package"] for rows in results.values() for row in rows}
        # The exact set the OSRB audit flagged: AGPL-3.0, Elastic-2.0 and
        # Confluent Community, none of which any lockfile records.
        for expected in (
            "docker.io/grafana/grafana",
            "minio/minio",
            "docker.elastic.co/kibana/kibana",
            "docker.elastic.co/logstash/logstash",
            "confluentinc/cp-kafka",
        ):
            self.assertIn(expected, found)
        # Locally built images must not be attributed to a public registry.
        self.assertNotIn("vss-kafka-topic-init", found)

    def test_every_workflow_parses_and_local_actions_are_excluded(self) -> None:
        results = self._parse_all("parse_actions_workflow")

        self.assertTrue(results, "no Actions workflow discovered")
        found = {row["package"] for rows in results.values() for row in rows}
        self.assertIn("actions/checkout", found)
        self.assertFalse([name for name in found if name.startswith(".")])

    def test_no_row_anywhere_in_the_tree_claims_a_license(self) -> None:
        rows = [
            row
            for parser_name in (
                "parse_dockerfile",
                "parse_compose",
                "parse_helm_chart",
                "parse_precommit",
                "parse_actions_workflow",
            )
            for file_rows in self._parse_all(parser_name).values()
            for row in file_rows
        ]

        self.assertTrue(rows)
        guessed = [row for row in rows if row["new_license"] or row["risk"] != "Unknown"]
        self.assertEqual([], guessed)



class RowKeyKeepsModuleTest(unittest.TestCase):
    """A dependency used by two modules is two rows, not one.

    OSRB approves per component, so `wheel` pip-installed into four images is
    four things to approve. The key here originally omitted `module`, so the
    first module parsed won and the rest vanished with nothing reporting a
    dropped duplicate -- 87 of 450 rows on the real tree, across 54 packages.
    Because the downstream diff in osrb_scan IS module-aware, the same omission
    also meant a dependency newly adopted by a second module produced no diff
    row at all: it looked unchanged.
    """

    def _row(self, module, package="wheel", version="0.45.1"):
        return {
            "source_kind": osrb_sources.KIND_CONTAINER,
            "language": "Python",
            "package": package,
            "new_version": version,
            "module": module,
        }

    def test_same_package_in_two_modules_has_two_keys(self) -> None:
        a = osrb_sources._row_key(self._row("services/agent"))
        b = osrb_sources._row_key(self._row("services/alert"))
        self.assertNotEqual(a, b)

    def test_same_package_in_the_same_module_still_dedupes(self) -> None:
        # Two Dockerfiles in one component installing the same pinned package
        # is one thing to approve, and must not double-report.
        a = osrb_sources._row_key(self._row("services/agent"))
        b = osrb_sources._row_key(self._row("services/agent"))
        self.assertEqual(a, b)

    def test_module_is_actually_part_of_the_key(self) -> None:
        self.assertIn("services/agent", osrb_sources._row_key(self._row("services/agent")))

    def test_a_row_without_module_does_not_raise(self) -> None:
        # osrb_sources rows always carry module today, but the key is called on
        # rows from several parsers; a KeyError here would take down the gate.
        row = self._row("services/agent")
        del row["module"]
        self.assertEqual(osrb_sources._row_key(row)[-1], "")

if __name__ == "__main__":
    unittest.main()