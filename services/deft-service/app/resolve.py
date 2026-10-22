"""Resolves a FineTuneRequest (profile+overrides XOR workflow+config) into
a concrete (workflow_name, ExperimentConfig) pair — the same "resolved
config, frozen at creation time" idea documented on
RunDetailsResponse.resolved_config in openapi.json.
"""

from __future__ import annotations

from app.data.profiles import PROFILES
from app.data.workflows import WORKFLOWS
from app.errors import invalid_request
from app.schemas.config_models import ExperimentConfig
from app.schemas.fine_tune_models import FineTuneRequest


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def resolve_experiment_config(req: FineTuneRequest) -> tuple[str, ExperimentConfig]:
    if req.profile is not None:
        if req.config is not None:
            raise invalid_request("'config' must not be set together with 'profile'.", ["body", "config"])
        profile = PROFILES.get(req.profile)
        if profile is None:
            raise invalid_request(f"Profile '{req.profile}' was not found.", ["body", "profile"])
        workflow_name = req.workflow or profile.workflow
        if workflow_name not in WORKFLOWS:
            raise invalid_request(f"Workflow '{workflow_name}' was not found.", ["body", "workflow"])
        base = profile.config.model_dump(mode="json")
        if req.overrides is not None:
            override_dict = req.overrides.model_dump(mode="json", exclude_unset=True)
            merged = _deep_merge(base, override_dict)
        else:
            merged = base
        return workflow_name, ExperimentConfig.model_validate(merged)

    if req.config is None:
        raise invalid_request("Either 'profile' or 'config' must be provided.", ["body"])
    if req.overrides is not None:
        raise invalid_request("'overrides' only applies when 'profile' is set.", ["body", "overrides"])
    if req.workflow is None:
        raise invalid_request("'workflow' is required when 'config' is provided directly.", ["body", "workflow"])
    if req.workflow not in WORKFLOWS:
        raise invalid_request(f"Workflow '{req.workflow}' was not found.", ["body", "workflow"])
    return req.workflow, req.config
