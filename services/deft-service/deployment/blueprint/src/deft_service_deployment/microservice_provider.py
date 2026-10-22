"""Microservice catalog owned by DEFT Service."""

from blueprint_builder import MicroserviceRegistration

from .microservices import DeftServiceReal

MICROSERVICES = (MicroserviceRegistration("deft-service", DeftServiceReal),)

__all__ = ["MICROSERVICES"]
