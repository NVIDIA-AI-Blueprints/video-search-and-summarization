"""Shared fixtures for VST security BDD tests."""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest
import requests

logger = logging.getLogger(__name__)


@dataclass
class SecurityTestContext:
    """Per-scenario context for security tests."""

    response: Optional[requests.Response] = None
    status_code: int = 0
    response_json: Any = None
    baseline_sensor_count: Optional[int] = None
    baseline_sensor_ids: List[str] = field(default_factory=list)
    injected_sensor_id: Optional[str] = None
    injected_sensor_name: Optional[str] = None
    attempted_filename: Optional[str] = None
    protect_response: Optional[requests.Response] = None
    protect_status_code: int = 0
    storage_info_status_code: int = 0


@pytest.fixture(scope="function")
def sec_context() -> SecurityTestContext:
    """Fresh context for each security scenario."""
    return SecurityTestContext()


@pytest.fixture(scope="session")
def security_test_params(config: Dict[str, Any]) -> Dict[str, Any]:
    """Reuse the unit_tests timeout setting; security checks are simple HTTP calls."""
    params = config.get("tests", {}).get("unit_tests", {}).get("test_parameters", {})
    return {"timeout": params.get("timeout", 30)}
