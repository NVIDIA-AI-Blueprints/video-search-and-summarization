# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""Real-mode DEFT service project for the complete DEFT AOI deployment."""

from collections import OrderedDict
from dataclasses import dataclass

from blueprint_builder import build_from_cli
from blueprint_builder.data_models import Blueprint

from ..microservices import DeftServiceReal


def _deft_service_for_data_flywheel() -> DeftServiceReal:
    """Register the shared DFW bearer token as a non-admin DEFT principal."""
    deft = DeftServiceReal()
    env = OrderedDict(deft.env)
    env["DEFT_API_KEYS"] = deft.env_config(
        "DEFT_API_KEYS",
        value="${DEFT_API_KEY:?DEFT_API_KEY must be set}:data-flywheel",
        secret_key_ref=None,
        export_to_env_file=False,
        desc=(
            "Static DEFT credential derived from the Data Flywheel bearer token. "
            "The integration profile registers it as the non-admin data-flywheel "
            "principal."
        ),
    )
    return deft.with_updates(env=env)


@dataclass(frozen=True, slots=True)
class DeftServiceComponent:
    """Typed real-mode DEFT graph and its training-control API service."""

    blueprint: Blueprint
    service: DeftServiceReal


def create_component() -> DeftServiceComponent:
    """Create real-mode DEFT and expose its integration endpoint explicitly."""
    service = _deft_service_for_data_flywheel()
    blueprint = Blueprint(
        name="deft-aoi-deft-service",
        description=(
            "Real-mode DEFT training control plane that launches sibling TAO "
            "containers through the host Docker socket."
        ),
        microservices=[service],
    )
    return DeftServiceComponent(blueprint=blueprint, service=service)


def create_component_blueprint() -> Blueprint:
    """Create the Compose-only real-mode DEFT project for parent integration."""
    return create_component().blueprint


def create_blueprint() -> Blueprint:
    """Create DEFT with its required AOI Storage endpoint left selectable.

    Build this owner Blueprint with ``--external-connector storage-backend``
    or ``--external-connector storage-backend=<host>:<port>``. The complete
    DEFT AOI integration connects this same service internally instead.
    """
    return create_component().blueprint


if __name__ == "__main__":
    build_from_cli(create_blueprint)
