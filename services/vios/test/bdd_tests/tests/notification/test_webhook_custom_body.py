# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Custom webhook body template tests driven by the file-sensor lifecycle.

Every template case, valid and invalid, is declared in
data/webhook_bdd_config.json and must already be applied to the deployed
notification config (scripts/update_notification_config.py) when VST starts.
Valid cases are verified differentially: the body VST delivers to a custom
receiver must equal the body computed by the reference implementations in
webhook_test_utils.py from the default receiver's capture of the same event —
render_body_template for a configured body (which also wins over
user_defined_metadata), merge_user_metadata for the metadata-only passthrough.
Invalid cases must be skipped at configuration load, so their receivers see
nothing while the default sibling still delivers.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

import pytest
from pytest_bdd import given, scenarios, then

from ..test_utils import assert_with_detailed_failure
from .conftest import WebhookTestContext
from .webhook_test_utils import (
    CapturedWebhookRequest,
    WebhookReceiver,
    merge_user_metadata,
    render_body_template,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.notification, pytest.mark.webhook]

scenarios("../../features/notification/webhook_custom_body.feature")

BDD_CONFIG_FILE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "webhook_bdd_config.json"
)


@pytest.fixture(scope="session")
def custom_body_cases() -> Dict[str, List[Dict[str, Any]]]:
    """Return the valid and invalid template cases from the shared BDD config."""
    return json.loads(BDD_CONFIG_FILE.read_text())["custom_body_cases"]


def _wait_for_default_notification(
    context: WebhookTestContext,
    webhook_receiver: WebhookReceiver,
    notification_test_params: Dict[str, Any],
    change: str,
) -> Dict[str, Any]:
    """Capture the default tagged body via the default receiver for one event.

    The returned body is the notification plus the webhook_id tag. A custom
    body renders from the notification alone (strip the tag first), while the
    user_defined_metadata passthrough merges into the tagged body as-is.
    """
    path = notification_test_params["webhook_paths"][change]
    timeout = notification_test_params["delivery_timeout_sec"]

    def matches(request: CapturedWebhookRequest) -> bool:
        if request.path != path or not isinstance(request.json_body, dict):
            return False
        event = request.json_body.get("event")
        return (
            isinstance(event, dict)
            and event.get("change") == change
            and event.get("camera_id") == context.sensor_id
        )

    try:
        request = webhook_receiver.wait_for(
            predicate=matches, start_sequence=context.receiver_cursor, timeout=timeout
        )
    except TimeoutError as exc:
        captured = [
            item.summary()
            for item in webhook_receiver.requests_since(context.receiver_cursor)
        ]
        assert_with_detailed_failure(
            False,
            test_name=f"default {change} webhook delivery",
            expected=(
                f"Default webhook path={path!r}, camera_id={context.sensor_id!r}, "
                f"change={change!r} within {timeout}s"
            ),
            actual=str(exc),
            additional_info=f"Captured requests: {captured}",
        )
        raise AssertionError("unreachable")

    return dict(request.json_body)


@given("the custom body test cases are loaded")
def custom_body_cases_are_loaded(
    custom_body_cases: Dict[str, List[Dict[str, Any]]],
) -> None:
    assert custom_body_cases["valid"], f"No valid cases in {BDD_CONFIG_FILE}"
    assert custom_body_cases["invalid"], f"No invalid cases in {BDD_CONFIG_FILE}"


def _expected_case_body(case: Dict[str, Any], tagged_body: Dict[str, Any]) -> Any:
    """Return the body the case must deliver for the captured default body."""
    if "body" in case:
        # The custom body is the complete request body, rendered from the
        # notification alone; a configured user_defined_metadata is ignored.
        notification = dict(tagged_body)
        notification.pop("webhook_id", None)
        return render_body_template(case["body"], notification)
    # Backward compat: default tagged body with user_defined_metadata merged
    # verbatim into event.metadata.
    return merge_user_metadata(tagged_body, case["user_defined_metadata"])


def _assert_valid_cases_delivered(
    groups: List[str],
    context: WebhookTestContext,
    webhook_receiver: WebhookReceiver,
    notification_test_params: Dict[str, Any],
    custom_body_cases: Dict[str, List[Dict[str, Any]]],
) -> None:
    timeout = notification_test_params["delivery_timeout_sec"]
    tagged_bodies: Dict[str, Dict[str, Any]] = {}
    cases = [
        case for case in custom_body_cases["valid"] if case["event_group"] in groups
    ]
    assert cases, f"No valid cases declared for {groups} in {BDD_CONFIG_FILE}"
    failures = []

    for case in cases:
        change = case["event_group"]
        if change not in tagged_bodies:
            tagged_bodies[change] = _wait_for_default_notification(
                context, webhook_receiver, notification_test_params, change
            )
        expected = _expected_case_body(case, tagged_bodies[change])

        try:
            request = webhook_receiver.wait_for(
                predicate=lambda captured, case=case: (
                    captured.path == case["path"]
                    and captured.method == case["method"]
                ),
                start_sequence=context.receiver_cursor,
                timeout=timeout,
            )
        except TimeoutError as exc:
            failures.append(
                {"description": f"{case['case']}: no request at {case['path']!r}: {exc}"}
            )
            continue

        if request.json_body != expected:
            failures.append(
                {
                    "description": (
                        f"{case['case']}: rendered body mismatch at {case['path']!r}: "
                        f"expected {expected!r}, got {request.json_body!r}"
                    )
                }
            )

    assert_with_detailed_failure(
        not failures,
        test_name=f"valid custom body templates ({', '.join(groups)})",
        expected=(
            f"each of the {len(cases)} valid cases delivered with exactly the body "
            f"computed by the reference implementation in webhook_test_utils.py"
        ),
        actual=f"{len(failures)} case(s) failed",
        failed_items=failures,
    )


@then(
    "every valid camera_add and camera_streaming custom body webhook "
    "delivers its rendered body"
)
def valid_add_and_streaming_custom_bodies_are_delivered(
    context: WebhookTestContext,
    webhook_receiver: WebhookReceiver,
    notification_test_params: Dict[str, Any],
    custom_body_cases: Dict[str, List[Dict[str, Any]]],
) -> None:
    _assert_valid_cases_delivered(
        ["camera_add", "camera_streaming"],
        context,
        webhook_receiver,
        notification_test_params,
        custom_body_cases,
    )


@then("every valid camera_remove custom body webhook delivers its rendered body")
def valid_remove_custom_bodies_are_delivered(
    context: WebhookTestContext,
    webhook_receiver: WebhookReceiver,
    notification_test_params: Dict[str, Any],
    custom_body_cases: Dict[str, List[Dict[str, Any]]],
) -> None:
    _assert_valid_cases_delivered(
        ["camera_remove"],
        context,
        webhook_receiver,
        notification_test_params,
        custom_body_cases,
    )


@then("the default camera_add webhook is delivered")
def default_camera_add_is_delivered(
    context: WebhookTestContext,
    webhook_receiver: WebhookReceiver,
    notification_test_params: Dict[str, Any],
) -> None:
    _wait_for_default_notification(
        context, webhook_receiver, notification_test_params, "camera_add"
    )


@then("no invalid custom body webhook is delivered")
def invalid_custom_bodies_are_not_delivered(
    context: WebhookTestContext,
    webhook_receiver: WebhookReceiver,
    notification_test_params: Dict[str, Any],
    custom_body_cases: Dict[str, List[Dict[str, Any]]],
) -> None:
    invalid_paths = {case["path"]: case["case"] for case in custom_body_cases["invalid"]}
    timeout = notification_test_params["filter_absence_timeout_sec"]
    try:
        request = webhook_receiver.wait_for(
            predicate=lambda captured: captured.path in invalid_paths,
            start_sequence=context.receiver_cursor,
            timeout=timeout,
        )
    except TimeoutError:
        return

    assert_with_detailed_failure(
        False,
        test_name="invalid custom body templates are skipped",
        expected=(
            f"No delivery to any of the {len(invalid_paths)} invalid-template "
            f"receivers during {timeout}s after the default camera_add arrived"
        ),
        actual=(
            f"case {invalid_paths[request.path]!r} received {request.summary()} "
            f"body={request.json_body!r}"
        ),
        additional_info=(
            "An invalid body template must be rejected at configuration load and "
            "its request entry skipped"
        ),
    )
