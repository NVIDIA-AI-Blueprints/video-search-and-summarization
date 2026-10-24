"""Blueprint catalog owned by DEFT Service."""

from blueprint_builder import BlueprintFactory

from .blueprints import create_blueprint

BLUEPRINTS = (
    BlueprintFactory(
        id="deft-aoi-deft-service",
        description=(
            "Compose-only real-mode DEFT training control plane. Its required "
            "AOI Storage connector can be supplied at build time."
        ),
        factory=create_blueprint,
        supported_backends=frozenset({"docker"}),
    ),
)

__all__ = ["BLUEPRINTS"]
