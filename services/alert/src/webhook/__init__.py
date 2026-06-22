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

__all__ = ["OpenClawNotifier", "WebhookKafkaForwarder"]


def __getattr__(name: str):
    if name == "OpenClawNotifier":
        from webhook.openclaw_notifier import OpenClawNotifier

        return OpenClawNotifier
    if name == "WebhookKafkaForwarder":
        from webhook.consumer import WebhookKafkaForwarder

        return WebhookKafkaForwarder
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
