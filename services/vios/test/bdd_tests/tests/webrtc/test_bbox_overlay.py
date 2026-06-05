# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
BDD tests for VST bbox overlay rendering.

Covers BDD-GAP-050, BDD-GAP-051, BDD-GAP-052.

These scenarios require a deployment that has stored bbox/overlay metadata
for a sensor (typically from a Metropolis perception pipeline). The static
BDD test environment does not provide that metadata, so each scenario
skips at runtime unless -m needs_bbox_metadata is supplied AND the
relevant override values are present in config.json under
tests.bbox_overlay_tests.test_parameters.
"""
import logging

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

logger = logging.getLogger(__name__)

scenarios('../../features/webrtc/bbox_overlay.feature')


class BBoxContext:
    def __init__(self):
        self.stream_id = None
        self.filter_value = None
        self.last_picture = None


@pytest.fixture
def context():
    return BBoxContext()


@given('the VST API is configured for bbox overlay tests')
def configure_bbox(context, api_config, config):
    """Confirm bbox metadata fixture is available, otherwise skip."""
    params = (
        config.get('tests', {})
        .get('bbox_overlay_tests', {})
        .get('test_parameters', {})
    )
    fixture = params.get('bbox_stream_id')
    if not fixture:
        pytest.skip(
            "No bbox_stream_id configured. To run these tests, add\n"
            "  tests.bbox_overlay_tests.test_parameters.bbox_stream_id\n"
            "to config.json with the streamId of a sensor that has stored "
            "bbox metadata, and run with -m needs_bbox_metadata."
        )
    context.stream_id = fixture


# All scenarios below also depend on metadata that does not exist in the
# standard BDD environment. They are kept as scaffolding so that promoting
# them to active tests only requires the fixture wiring above and the
# pixel-color assertion logic below.

@given(parsers.parse('a stream has stored bbox metadata with classType "{class_type}"'))
def need_classtype_metadata(context, class_type):
    pytest.skip(
        f"Requires stored bbox metadata for classType '{class_type}'. "
        f"Seed metadata via the perception pipeline."
    )


@given('an active stream has live bbox metadata')
def need_live_bbox_metadata(context):
    pytest.skip(
        "Requires an active stream emitting live bbox metadata."
    )


@given('a recorded stream has stored bbox metadata')
def need_recorded_bbox_metadata(context):
    pytest.skip(
        "Requires a recorded stream with stored bbox metadata."
    )


@when(parsers.parse('overlay is requested filtered by classType "{filter_value}"'))
def overlay_classtype_filter(context, filter_value):
    context.filter_value = filter_value


@when('the live picture is requested with overlay=true')
def live_picture_overlay(context):
    pass


@when('the replay picture is requested with overlay=true')
def replay_picture_overlay(context):
    pass


@then('the overlay is rendered for the filter')
def overlay_rendered(context):
    pass


@then('the JPEG contains a region of the expected bbox color')
def jpeg_has_bbox_color(context):
    pass
