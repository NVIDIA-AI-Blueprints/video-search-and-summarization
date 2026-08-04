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

"""Unit tests for the lazy ``webhook`` package exports.

``webhook/__init__.py`` resolves its two public names through ``__getattr__``
so that importing the package does not pull in ``confluent_kafka`` or
``requests`` unless the webhook feature is actually switched on. The enhancer
entry point relies on this (``from webhook import OpenClawNotifier,
WebhookKafkaForwarder``), so the lazy path is worth pinning down.
"""

import pytest

import webhook


class TestLazyExports:
    def test_openclaw_notifier_is_resolved(self):
        from webhook.openclaw_notifier import OpenClawNotifier

        assert webhook.OpenClawNotifier is OpenClawNotifier

    def test_webhook_kafka_forwarder_is_resolved(self):
        from webhook.consumer import WebhookKafkaForwarder

        assert webhook.WebhookKafkaForwarder is WebhookKafkaForwarder

    def test_unknown_attribute_raises_attribute_error(self):
        with pytest.raises(AttributeError, match="has no attribute 'Nope'"):
            webhook.Nope

    def test_all_lists_both_public_names(self):
        assert set(webhook.__all__) == {"OpenClawNotifier", "WebhookKafkaForwarder"}

    def test_every_name_in_all_is_importable(self):
        for name in webhook.__all__:
            assert getattr(webhook, name) is not None
