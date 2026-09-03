# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lightweight public API for bounded memory introspection."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING
from typing import Any

__all__ = [
    "AnswerSynthesizer",
    "GroundedGap",
    "IntrospectionRequest",
    "IntrospectionResult",
    "IntrospectionSettings",
    "IntrospectionVLMRunner",
    "InvalidJudgeResponseError",
    "MemoryEvidence",
    "OpenAIIntrospectionClient",
    "SufficiencyDecision",
    "SufficiencyJudge",
    "VLMEvidence",
    "introspect",
]

_LAZY_EXPORTS = {
    "GroundedGap": ".models",
    "IntrospectionRequest": ".models",
    "IntrospectionResult": ".models",
    "IntrospectionSettings": ".models",
    "MemoryEvidence": ".models",
    "SufficiencyDecision": ".models",
    "VLMEvidence": ".models",
    "AnswerSynthesizer": ".protocols",
    "IntrospectionVLMRunner": ".protocols",
    "SufficiencyJudge": ".protocols",
    "InvalidJudgeResponseError": ".judge",
    "OpenAIIntrospectionClient": ".judge",
    "introspect": ".orchestrator",
}

if TYPE_CHECKING:
    from .judge import InvalidJudgeResponseError
    from .judge import OpenAIIntrospectionClient
    from .models import GroundedGap
    from .models import IntrospectionRequest
    from .models import IntrospectionResult
    from .models import IntrospectionSettings
    from .models import MemoryEvidence
    from .models import SufficiencyDecision
    from .models import VLMEvidence
    from .orchestrator import introspect
    from .protocols import AnswerSynthesizer
    from .protocols import IntrospectionVLMRunner
    from .protocols import SufficiencyJudge


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
