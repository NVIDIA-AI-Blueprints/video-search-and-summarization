# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for the heartbeat request models in ``web.schemas.schemas``.

These models describe the periodic image-sampling schedule submitted for a
sensor. The nesting (request -> config -> kwargs) means a malformed inner
block must surface as a validation error rather than silently producing a
schedule with missing prompts, so required/optional fields and nested
coercion are pinned here.
"""

import pytest
from pydantic import ValidationError

from web.schemas.schemas import HeartbeatConfig, HeartbeatKwargs, HeartbeatRequest


class TestHeartbeatKwargs:
    def test_type_defaults_to_image_sampling(self):
        kwargs = HeartbeatKwargs(
            sensor_name="cam-1", sensor_location="Lobby", prompt="Describe the scene."
        )
        assert kwargs.type == "imageSampling"

    def test_type_can_be_overridden(self):
        kwargs = HeartbeatKwargs(
            sensor_name="cam-1", sensor_location="Lobby", prompt="p", type="videoSampling"
        )
        assert kwargs.type == "videoSampling"

    @pytest.mark.parametrize("missing", ["sensor_name", "sensor_location", "prompt"])
    def test_required_fields_are_enforced(self, missing):
        fields = {"sensor_name": "cam-1", "sensor_location": "Lobby", "prompt": "p"}
        del fields[missing]
        with pytest.raises(ValidationError):
            HeartbeatKwargs(**fields)

    def test_documented_example_validates(self):
        example = HeartbeatKwargs.model_config["json_schema_extra"]["example"]
        assert HeartbeatKwargs(**example).sensor_name == "cam_lobby_01"


class TestHeartbeatConfig:
    def _kwargs(self):
        return HeartbeatKwargs(sensor_name="cam-1", sensor_location="Lobby", prompt="p")

    def test_task_and_args_have_defaults(self):
        config = HeartbeatConfig(schedule=60.0, kwargs=self._kwargs())
        assert config.task == "utils.scheduler.tasks.emit_heartbeat"
        assert config.args == []

    def test_schedule_is_required(self):
        with pytest.raises(ValidationError):
            HeartbeatConfig(kwargs=self._kwargs())

    def test_schedule_accepts_an_integer(self):
        assert HeartbeatConfig(schedule=60, kwargs=self._kwargs()).schedule == 60.0

    def test_non_numeric_schedule_is_rejected(self):
        with pytest.raises(ValidationError):
            HeartbeatConfig(schedule="soon", kwargs=self._kwargs())

    def test_kwargs_are_coerced_from_a_dict(self):
        config = HeartbeatConfig(
            schedule=60.0,
            kwargs={"sensor_name": "cam-1", "sensor_location": "Lobby", "prompt": "p"},
        )
        assert isinstance(config.kwargs, HeartbeatKwargs)

    def test_invalid_nested_kwargs_are_rejected(self):
        with pytest.raises(ValidationError):
            HeartbeatConfig(schedule=60.0, kwargs={"sensor_name": "cam-1"})

    def test_documented_example_validates(self):
        example = HeartbeatConfig.model_config["json_schema_extra"]["example"]
        assert HeartbeatConfig(**example).schedule == 60.0


class TestHeartbeatRequest:
    def test_round_trips_a_full_payload(self):
        payload = HeartbeatRequest.model_config["json_schema_extra"]["example"]
        request = HeartbeatRequest(**payload)

        assert request.name == "cam_lobby_01"
        assert request.config.kwargs.prompt == "Describe the current scene."
        assert request.model_dump()["config"]["kwargs"]["type"] == "imageSampling"

    def test_name_is_required(self):
        with pytest.raises(ValidationError):
            HeartbeatRequest(
                config={
                    "schedule": 60.0,
                    "kwargs": {
                        "sensor_name": "cam-1",
                        "sensor_location": "Lobby",
                        "prompt": "p",
                    },
                }
            )

    def test_config_is_required(self):
        with pytest.raises(ValidationError):
            HeartbeatRequest(name="cam-1")
