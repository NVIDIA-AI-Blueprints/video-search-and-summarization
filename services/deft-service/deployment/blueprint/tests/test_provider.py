"""Provider and Compose regression tests for DEFT Service deployment definitions."""

from pathlib import Path

import pytest
from blueprint_builder import BlueprintFactory, MicroserviceRegistration
from blueprint_builder.backends.docker_compose.builder import DockerComposeBuilder
from blueprint_builder.backends.helm_chart.builder import HelmChartBuilder
from blueprint_builder.data_models import (
    Blueprint,
    Connection,
    ContainerImage,
    Microservice,
    Probes,
    RestartPolicy,
    Service,
    ServicePort,
)

from deft_service_deployment.blueprint_provider import BLUEPRINTS
from deft_service_deployment.blueprints import (
    create_blueprint,
    create_component,
    create_component_blueprint,
)
from deft_service_deployment.microservice_provider import MICROSERVICES
from deft_service_deployment.microservices import DeftServiceReal

RUNTIME_DIR = (
    "${DEFT_RUNTIME_DIR:?Set DEFT_RUNTIME_DIR to an absolute host directory for DEFT runtime data}"
)
SOURCE_DIR = "${DEFT_SERVICE_DIR:?Set DEFT_SERVICE_DIR to the deft-service checkout}"


def _component_with_storage() -> Blueprint:
    """Compose the provider-owned service with an internal AOI Storage peer."""
    component = create_component()
    storage = Microservice(
        name="aoi-storage",
        description="AOI Storage test target.",
        container_image=ContainerImage(
            image="example.invalid/aoi-storage",
            tag="latest",
        ),
        service=Service(
            name="aoi-storage",
            ports=[
                ServicePort(
                    name="http",
                    port=8000,
                    publish_to_host=False,
                    description="AOI Storage HTTP API.",
                )
            ],
        ),
        probes=Probes(),
    )
    blueprint = Blueprint(
        name="deft-real",
        description="Real-mode DEFT Compose contract.",
        microservices=[storage, component.service],
    )
    blueprint.add_connection(
        Connection(
            source_ms=component.service,
            source_connector="storage-backend",
            target_ms=storage,
            target_port="http",
        ),
        readiness_check=False,
    )
    return blueprint


def test_microservice_catalog_exports_concrete_classes() -> None:
    """Every advertised service can be instantiated independently."""
    assert MICROSERVICES
    assert all(isinstance(item, MicroserviceRegistration) for item in MICROSERVICES)
    assert all(issubclass(item.microservice_type, Microservice) for item in MICROSERVICES)
    assert [item.microservice_type().name for item in MICROSERVICES] == ["deft-service"]


def test_component_factory_constructs_declared_graph() -> None:
    """The package API constructs a fresh graph for parent composition."""
    blueprint = create_component_blueprint()
    second = create_component_blueprint()

    assert isinstance(blueprint, Blueprint)
    assert blueprint.name == "deft-aoi-deft-service"
    assert blueprint is not second
    assert [service.name for service in blueprint.microservices] == ["deft-service"]


def test_component_exposes_typed_deft_service_handle() -> None:
    """Parent Blueprints connect through an explicit service object."""
    component = create_component()

    assert component.blueprint[component.service.name] is component.service
    assert isinstance(component.service, DeftServiceReal)
    assert component.service.env["DEFT_API_KEYS"].value == (
        "${DEFT_API_KEY:?DEFT_API_KEY must be set}:data-flywheel"
    )
    assert component.service.env["DEFT_API_KEYS"].secret_key_ref is None
    assert component.service.env["DEFT_API_KEYS"].export_to_env_file is False


def test_standalone_factory_leaves_required_storage_selectable() -> None:
    """The standalone API returns a fresh owner graph ready for external storage."""
    blueprint = create_blueprint()

    bindings = blueprint.bind_external_connector_selector(
        "storage-backend",
        "aoi-storage.example:8000",
    )

    assert blueprint.name == "deft-aoi-deft-service"
    assert len(bindings) == 1
    assert bindings[0].source_ms.name == "deft-service"
    blueprint.validate_for_build(backend="docker")


def test_blueprint_catalog_exposes_compose_graph_with_external_storage() -> None:
    """The owner graph becomes valid when its required storage edge is externalized."""
    assert all(isinstance(item, BlueprintFactory) for item in BLUEPRINTS)
    registration = BLUEPRINTS[0]
    blueprint = registration.factory()

    assert registration.id == "deft-aoi-deft-service"
    assert registration.supported_backends == frozenset({"docker"})
    blueprint.bind_external_connector_selector(
        "storage-backend",
        "aoi-storage.example:8000",
    )
    blueprint.validate_for_build(backend="docker")


def test_real_service_models_docker_build_and_runtime_options() -> None:
    """Real execution is built locally and exposes only its Compose HTTP API."""
    service = DeftServiceReal()
    image = service.container_image

    assert service.supported_backends == {"docker"}
    assert image.image == "deft-service"
    assert image.tag == "local"
    assert image.build_config.context == SOURCE_DIR
    assert image.build_config.dockerfile == "Dockerfile"
    assert image.build_config.args == {}
    assert image.build_config.additional_contexts == {}
    assert service.resources is None
    assert service.restart_policy == RestartPolicy.always
    assert service.has_openapi_endpoint is True
    assert [(port.name, port.port, port.host_port) for port in service.service.ports] == [
        ("http", 8000, "${DEFT_SERVICE_PORT:-9150}")
    ]


def test_real_service_requires_identity_mounted_workspace_and_static_auth() -> None:
    """Workspace paths remain host-visible and real mode cannot be relaxed by env."""
    env = DeftServiceReal().env

    assert env["DEFT_EXECUTION_MODE"].value == "real"
    assert env["DEFT_EXECUTION_MODE"].allowed_values == ["real"]
    assert env["DEFT_BACKEND_TYPE"].value == "docker"
    assert env["DEFT_BACKEND_TYPE"].allowed_values == ["docker"]
    assert env["DEFT_RUNTIME_DIR"].value == RUNTIME_DIR
    assert env["DEFT_ALLOWED_WORKSPACE_ROOT"].value == RUNTIME_DIR
    assert env["DEFT_MATERIALIZE_ROOT"].value == f"{RUNTIME_DIR}/materialize"
    assert env["DEFT_STORAGE_ROOT"].value == f"file://{RUNTIME_DIR}/storage"
    assert env["DEFT_DB_PATH"].value == f"{RUNTIME_DIR}/deft_service.db"
    assert env["DEFT_FIPS_ZERO_FILE"].value == f"{RUNTIME_DIR}/deft_fips_zero"
    assert env["DEFT_BACKBONE_CACHE"].value == (f"{RUNTIME_DIR}/cache/c_radio_v2_b.safetensors")
    assert env["DEFT_AUTH_MODE"].value == "static"
    assert env["DEFT_AUTH_MODE"].allowed_values == ["static"]
    assert env["DEFT_API_KEYS"].secret_key_ref.optional is False
    assert env["HF_TOKEN"].secret_key_ref.optional is True


def test_real_service_preserves_tunable_stage_defaults() -> None:
    """Real TAO execution options remain visible and overridable in Compose."""
    env = DeftServiceReal().env

    names = (
        "DEFT_MAX_NUM_GPUS",
        "DEFT_GPU_SLOTS",
        "DEFT_STAGE_TIMEOUT_S",
        "DEFT_TAO_PYT_IMAGE",
        "DEFT_SIGLIP_MODEL",
    )
    assert {name: env[name].value for name in names} == {
        "DEFT_MAX_NUM_GPUS": "${DEFT_MAX_NUM_GPUS:-64}",
        "DEFT_GPU_SLOTS": "${DEFT_GPU_SLOTS:-1}",
        "DEFT_STAGE_TIMEOUT_S": "${DEFT_STAGE_TIMEOUT_S:-14400}",
        "DEFT_TAO_PYT_IMAGE": ("${DEFT_TAO_PYT_IMAGE:-nvcr.io/nvidia/tao/tao-toolkit:6.26.3-pyt}"),
        "DEFT_SIGLIP_MODEL": "${DEFT_SIGLIP_MODEL:-google/siglip-base-patch16-224}",
    }


def test_real_service_declares_required_storage_connector() -> None:
    """DEFT uses AOI Storage APIs without receiving object-store credentials."""
    connector = DeftServiceReal().connectors["storage-backend"]

    assert connector.required is True
    assert connector.target_type == "aoi-storage-backend"
    assert {name: config.value for name, config in connector.connection_env.items()} == {
        "DEFT_MATERIALIZE_PRESIGN_URL": "http://<host>:<port>/v1/get_presigned_urls",
        "DEFT_MATERIALIZE_STORAGE_URL": "http://<host>:<port>",
        "DEFT_PUBLISH_PRESIGN_URL": "http://<host>:<port>/v1/get_presigned_post_url",
    }


def test_real_service_models_host_daemon_mounts_and_health_probes() -> None:
    """Sibling containers share the Docker socket, workspace, and health contract."""
    service = DeftServiceReal()
    readiness = service.probes.readiness_probe
    liveness = service.probes.liveness_probe

    assert [volume.path for volume in service.volumes] == [
        "/var/run/docker.sock:/var/run/docker.sock",
        f"{RUNTIME_DIR}:{RUNTIME_DIR}",
    ]
    assert readiness.command[0:2] == ["python", "-c"]
    assert liveness.command[0:2] == ["python", "-c"]
    assert "http://localhost:8000/v1/health" in liveness.command[2]
    assert "timeout=4" in liveness.command[2]
    assert (readiness.initialDelaySeconds, readiness.periodSeconds) == (5, 10)
    assert (liveness.initialDelaySeconds, liveness.periodSeconds) == (15, 20)


def test_component_compose_resolves_internal_storage_and_runtime_contract() -> None:
    """Compose keeps host paths and resolves a component's internal storage peer."""
    compose = DockerComposeBuilder(_component_with_storage()).build()
    service = compose.services["deft-service"]

    assert service.build == {"context": SOURCE_DIR, "dockerfile": "Dockerfile"}
    assert service.environment["DEFT_EXECUTION_MODE"] == "real"
    assert service.environment["DEFT_AUTH_MODE"] == "static"
    assert service.environment["DEFT_API_KEYS"] == (
        "${DEFT_API_KEY:?DEFT_API_KEY must be set}:data-flywheel"
    )
    assert (
        service.environment["DEFT_MATERIALIZE_PRESIGN_URL"]
        == "http://aoi-storage:8000/v1/get_presigned_urls"
    )
    assert service.environment["DEFT_MATERIALIZE_STORAGE_URL"] == "http://aoi-storage:8000"
    assert (
        service.environment["DEFT_PUBLISH_PRESIGN_URL"]
        == "http://aoi-storage:8000/v1/get_presigned_post_url"
    )
    assert service.volumes == [
        "/var/run/docker.sock:/var/run/docker.sock",
        f"{RUNTIME_DIR}:{RUNTIME_DIR}",
    ]
    assert service.ports == ["${DEFT_SERVICE_PORT:-9150}:8000"]
    assert service.restart == "always"
    assert service.healthcheck.test[0:3] == ["CMD", "python", "-c"]


def test_standalone_compose_export_preserves_required_inputs(
    tmp_path: Path,
) -> None:
    """Exported artifacts retain fail-fast source, workspace, and auth variables."""
    blueprint = create_blueprint()
    blueprint.bind_external_connector_selector(
        "storage-backend",
        "aoi-storage.example:8000",
    )
    output = tmp_path / "artifacts" / "docker-compose.yml"

    DockerComposeBuilder(blueprint).export_to_file(output)

    rendered = output.read_text(encoding="utf-8")
    env_file = output.with_name(".env").read_text(encoding="utf-8")
    assert f"context: {SOURCE_DIR}" in rendered
    assert "/var/run/docker.sock:/var/run/docker.sock" in rendered
    assert rendered.count("${DEFT_RUNTIME_DIR:?") >= 2
    assert "DEFT_API_KEYS: ${DEFT_API_KEY:?DEFT_API_KEY must be set}:data-flywheel" in rendered
    assert "DEFT_MATERIALIZE_STORAGE_URL:" in rendered
    assert "${DEFT_MATERIALIZE_STORAGE_URL:-http://aoi-storage.example:8000}" in rendered
    assert "DEFT_SERVICE_DIR=" in env_file
    assert "DEFT_RUNTIME_DIR=" in env_file
    assert "# REQUIRED: set this before building the deployment." in env_file
    assert "DEFT_SERVICE_PORT=9150" in env_file


def test_real_service_rejects_helm_generation(tmp_path: Path) -> None:
    """Helm fails rather than dropping the service's host Docker requirements."""
    with pytest.raises(
        ValueError,
        match=r"cannot be built for Kubernetes/Helm.*deft-service",
    ):
        HelmChartBuilder(_component_with_storage()).export_to_directory(tmp_path / "chart")
