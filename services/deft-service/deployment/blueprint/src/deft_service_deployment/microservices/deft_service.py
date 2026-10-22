# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""Real-mode DEFT service for Docker Compose deployments.

DEFT launches TAO stage containers through the host Docker daemon. The service
therefore requires the Docker socket and an identity-mounted absolute runtime
directory: a workspace path created inside the DEFT container must resolve to
the same path on the Docker host when DEFT passes it to ``docker run -v``.

This contract is intentionally Docker Compose-only. Kubernetes support requires
a container-native execution backend rather than access to a host Docker daemon.
"""

from collections import OrderedDict

from blueprint_builder.data_models import (
    BuildConfig,
    ConnectorSpec,
    ContainerImage,
    EnvVarConfig,
    Microservice,
    MicroserviceConnectors,
    MicroserviceContainerImage,
    MicroserviceDescription,
    MicroserviceEnv,
    MicroserviceHasOpenAPIEndpoint,
    MicroserviceKeywords,
    MicroserviceName,
    MicroserviceProbes,
    MicroserviceRestartPolicy,
    MicroserviceService,
    MicroserviceSupportedBackends,
    MicroserviceVolumes,
    RestartPolicy,
    SecretKeyRef,
    Service,
    ServicePort,
)
from blueprint_builder.data_models.probes import ExecProbe, Probes
from blueprint_builder.data_models.volume import PathVolume
from pydantic import Field

DEFT_SERVICE_SOURCE_DIR = "${DEFT_SERVICE_DIR:?Set DEFT_SERVICE_DIR to the deft-service checkout}"
DEFT_RUNTIME_DIR = (
    "${DEFT_RUNTIME_DIR:?Set DEFT_RUNTIME_DIR to an absolute host directory for DEFT runtime data}"
)
DEFT_SERVICE_PORT = 8000


def _deft_service_image() -> ContainerImage:
    """Build the DEFT service image from its current source checkout."""
    return ContainerImage(
        image="deft-service",
        tag="local",
        build_config=BuildConfig(
            context=DEFT_SERVICE_SOURCE_DIR,
            dockerfile="Dockerfile",
        ),
    )


def _deft_service_env() -> OrderedDict[str, EnvVarConfig]:
    """Return the real-execution environment and host-path contract."""
    return OrderedDict(
        [
            (
                "DEFT_EXECUTION_MODE",
                EnvVarConfig(
                    value="real",
                    allowed_values=["real"],
                    export_to_env_file=False,
                    desc=(
                        "Run the AOI active-learning workflow through real TAO "
                        "stage containers instead of the API simulator."
                    ),
                ),
            ),
            (
                "DEFT_BACKEND_TYPE",
                EnvVarConfig(
                    value="docker",
                    allowed_values=["docker"],
                    export_to_env_file=False,
                    desc=(
                        "Launch TAO stages as sibling containers through the "
                        "mounted host Docker daemon."
                    ),
                ),
            ),
            (
                "DEFT_RUNTIME_DIR",
                EnvVarConfig(
                    value=DEFT_RUNTIME_DIR,
                    desc=(
                        "Absolute host directory identity-mounted into DEFT. It "
                        "must resolve to the same path on the host and in the "
                        "container so sibling TAO bind mounts remain valid."
                    ),
                ),
            ),
            (
                "DEFT_MATERIALIZE_ROOT",
                EnvVarConfig(
                    value=f"{DEFT_RUNTIME_DIR}/materialize",
                    export_to_env_file=False,
                    desc="Per-run AOI snapshots and training workspaces.",
                ),
            ),
            (
                "DEFT_ALLOWED_WORKSPACE_ROOT",
                EnvVarConfig(
                    value=DEFT_RUNTIME_DIR,
                    export_to_env_file=False,
                    desc=(
                        "Restrict local training workspaces to the identity-mounted "
                        "runtime tree before DEFT passes paths to the host daemon."
                    ),
                ),
            ),
            (
                "DEFT_STORAGE_ROOT",
                EnvVarConfig(
                    value=f"file://{DEFT_RUNTIME_DIR}/storage",
                    export_to_env_file=False,
                    desc="Default local artifact root used by the DEFT API contract.",
                ),
            ),
            (
                "DEFT_DB_PATH",
                EnvVarConfig(
                    value=f"{DEFT_RUNTIME_DIR}/deft_service.db",
                    export_to_env_file=False,
                    desc="Persistent single-instance SQLite state database.",
                ),
            ),
            (
                "DEFT_FIPS_ZERO_FILE",
                EnvVarConfig(
                    value=f"{DEFT_RUNTIME_DIR}/deft_fips_zero",
                    export_to_env_file=False,
                    desc=(
                        "Host-visible FIPS compatibility marker that DEFT may "
                        "mount into sibling TAO containers."
                    ),
                ),
            ),
            (
                "DEFT_AUTH_MODE",
                EnvVarConfig(
                    value="static",
                    allowed_values=["static"],
                    export_to_env_file=False,
                    desc=(
                        "Require bearer tokens configured through DEFT_API_KEYS. "
                        "The trusted demo mode is intentionally not exposed by "
                        "this real-execution deployment."
                    ),
                ),
            ),
            (
                "DEFT_API_KEYS",
                EnvVarConfig(
                    secret_key_ref=SecretKeyRef(
                        name="deft-api-keys",
                        key="DEFT_API_KEYS",
                        optional=False,
                    ),
                    desc=(
                        "Comma-separated '<token>:<subject>' credentials used by "
                        "static authentication. Prefix a subject with 'admin:' "
                        "to grant the deft:admin scope."
                    ),
                ),
            ),
            (
                "HF_TOKEN",
                EnvVarConfig(
                    secret_key_ref=SecretKeyRef(
                        name="hf-token",
                        key="HF_TOKEN",
                        optional=True,
                    ),
                    desc=(
                        "Optional Hugging Face token forwarded to TAO stage "
                        "containers when gated model assets are required."
                    ),
                ),
            ),
            (
                "DEFT_MAX_NUM_GPUS",
                EnvVarConfig(
                    value="${DEFT_MAX_NUM_GPUS:-64}",
                    desc="Maximum GPU count accepted in fine-tuning requests.",
                ),
            ),
            (
                "DEFT_GPU_SLOTS",
                EnvVarConfig(
                    value="${DEFT_GPU_SLOTS:-1}",
                    desc=(
                        "Maximum number of sibling GPU stage containers allowed "
                        "to execute concurrently."
                    ),
                ),
            ),
            (
                "DEFT_STAGE_TIMEOUT_S",
                EnvVarConfig(
                    value="${DEFT_STAGE_TIMEOUT_S:-14400}",
                    desc="Wall-clock timeout in seconds for each TAO stage container.",
                ),
            ),
            (
                "DEFT_TAO_PYT_IMAGE",
                EnvVarConfig(
                    value="${DEFT_TAO_PYT_IMAGE:-nvcr.io/nvidia/tao/tao-toolkit:6.26.3-pyt}",
                    desc="TAO PyTorch image launched for real training stages.",
                ),
            ),
            (
                "DEFT_SIGLIP_MODEL",
                EnvVarConfig(
                    value="${DEFT_SIGLIP_MODEL:-google/siglip-base-patch16-224}",
                    desc="SigLIP model identifier or child-container-visible local path.",
                ),
            ),
            (
                "DEFT_BACKBONE_CACHE",
                EnvVarConfig(
                    value=f"{DEFT_RUNTIME_DIR}/cache/c_radio_v2_b.safetensors",
                    export_to_env_file=False,
                    desc=(
                        "Persistent pretrained ChangeNet backbone cache inside "
                        "the identity-mounted runtime directory."
                    ),
                ),
            ),
        ]
    )


def _deft_storage_connector() -> ConnectorSpec:
    """Resolve DEFT materialization and publication through AOI Storage."""
    return ConnectorSpec(
        name="storage-backend",
        description=(
            "Internal AOI Storage HTTP API used to enumerate snapshots and obtain "
            "presigned download and upload requests without embedding object-store "
            "credentials in DEFT. Keep this connector on the trusted deployment "
            "network; HTTPS requires TLS support or termination in AOI Storage."
        ),
        required=True,
        target_type="aoi-storage-backend",
        connection_env={
            "DEFT_MATERIALIZE_PRESIGN_URL": EnvVarConfig(
                value="http://<host>:<port>/v1/get_presigned_urls",
                desc="AOI Storage endpoint that issues presigned GET URLs.",
            ),
            "DEFT_MATERIALIZE_STORAGE_URL": EnvVarConfig(
                value="http://<host>:<port>",
                desc="AOI Storage base URL used to enumerate snapshot objects.",
            ),
            "DEFT_PUBLISH_PRESIGN_URL": EnvVarConfig(
                value="http://<host>:<port>/v1/get_presigned_post_url",
                desc="AOI Storage endpoint that issues presigned artifact uploads.",
            ),
        },
    )


def _deft_service_probes() -> Probes:
    """Probe the unauthenticated health endpoint exposed by DEFT."""
    command = [
        "python",
        "-c",
        (
            "import urllib.request,sys; "
            "sys.exit(0 if urllib.request.urlopen("
            f"'http://localhost:{DEFT_SERVICE_PORT}/v1/health', timeout=4"
            ").status == 200 else 1)"
        ),
    ]
    return Probes(
        readiness_probe=ExecProbe(
            command=list(command),
            initialDelaySeconds=5,
            periodSeconds=10,
            timeoutSeconds=5,
            failureThreshold=6,
        ),
        liveness_probe=ExecProbe(
            command=list(command),
            initialDelaySeconds=15,
            periodSeconds=20,
            timeoutSeconds=5,
            failureThreshold=3,
        ),
    )


class DeftServiceReal(Microservice):
    """Run the DEFT AOI fine-tuning control plane in real Docker mode.

    The service itself does not consume a GPU. It controls sibling TAO
    containers through ``/var/run/docker.sock``; those child workloads use host
    GPUs outside Compose's resource reservation model. Deploy this only on a
    trusted single-host Docker system and coordinate its GPU use with any other
    workloads on that host.
    """

    name: MicroserviceName = "deft-service"
    description: MicroserviceDescription = (
        "Docker-hosted DEFT AOI fine-tuning API running the real active-learning "
        "pipeline through sibling TAO containers."
    )
    container_image: MicroserviceContainerImage = Field(default_factory=_deft_service_image)
    env: MicroserviceEnv = Field(default_factory=_deft_service_env)
    connectors: MicroserviceConnectors = Field(
        default_factory=lambda: {"storage-backend": _deft_storage_connector()}
    )
    service: MicroserviceService = Field(
        default_factory=lambda: Service(
            name="deft-service",
            ports=[
                ServicePort(
                    name="http",
                    port=DEFT_SERVICE_PORT,
                    host_port="${DEFT_SERVICE_PORT:-9150}",
                    description=(
                        "DEFT workflow, fine-tuning, status, result, and health HTTP API."
                    ),
                )
            ],
        )
    )
    volumes: MicroserviceVolumes = Field(
        default_factory=lambda: [
            PathVolume(
                name="deft-docker-socket",
                path="/var/run/docker.sock:/var/run/docker.sock",
                description=(
                    "Host Docker API socket used to launch sibling TAO containers. "
                    "Access to this socket is equivalent to host-level Docker control."
                ),
            ),
            PathVolume(
                name="deft-runtime",
                path=f"{DEFT_RUNTIME_DIR}:{DEFT_RUNTIME_DIR}",
                description=(
                    "Identity-mounted absolute runtime directory shared with sibling "
                    "TAO containers. A named volume cannot satisfy this host-path contract."
                ),
            ),
        ]
    )
    probes: MicroserviceProbes = Field(default_factory=_deft_service_probes)
    restart_policy: MicroserviceRestartPolicy = RestartPolicy.always
    keywords: MicroserviceKeywords = Field(
        default_factory=lambda: [
            "deft",
            "aoi",
            "active-learning",
            "fine-tuning",
            "tao",
        ]
    )
    has_openapi_endpoint: MicroserviceHasOpenAPIEndpoint = True
    supported_backends: MicroserviceSupportedBackends = Field(default_factory=lambda: {"docker"})
