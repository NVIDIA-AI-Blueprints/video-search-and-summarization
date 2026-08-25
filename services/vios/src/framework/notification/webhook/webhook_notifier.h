/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#pragma once

#include <any>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "notification_manager.h"
#include "async_http_client.h"

/*
 * Config-driven webhook fan-out notifier.
 *
 * Loads webhook definitions from the webhooks block of
 * configs/notification_config.json: a global enabled switch plus an items
 * array. Each item triggers on one alert type,
 * expressed as a key whose value narrows the event, e.g.
 * "camera_status_change": "camera_add", and carries a request array: every
 * matching event is posted to all receivers in that array through
 * AsyncHttpClient. A receiver may narrow further with a camera_type list;
 * it then only gets events whose event.camera_type is listed. An item's "id"
 * is copied into the delivered body under "webhook_id". A receiver may carry
 * a user_defined_metadata object; its members are merged verbatim into the
 * delivered body's event.metadata (created when absent), overwriting
 * same-named event-generated keys.
 *
 * A receiver may instead configure a custom "body" JSON template, rendered per
 * delivery against the raw notification and sent as the complete request body:
 * no webhook_id tagging and no user_defined_metadata merge. A placeholder is a
 * string whose whole value is "{{path.to.value}}", replaced type-preservingly
 * by the value at that dotted path, or by "" when the path is absent. Braces
 * are reserved: any other use of "{{" or "}}" in a template string or property
 * name fails validation at load and skips that receiver alone, as does nesting
 * deeper than 32 levels.
 *
 * deliverMessage() only enqueues HTTP work and returns true immediately: the
 * event-level 5 s retry loop in INotificationInterface is deliberately opted
 * out of. Retry policy lives here, per receiver: a completion with a transport
 * error or an HTTP status listed in retry.retry_on_status is resubmitted with
 * delayMs = retry.backoff_ms[attempt] until retry.max_attempts is exhausted;
 * any other non-2xx status is a permanent failure.
 */
class WebhookNotifier : public nv_vms::INotificationInterface
{
public:
    virtual ~WebhookNotifier();

    WebhookNotifier(const WebhookNotifier&) = delete;
    WebhookNotifier& operator=(const WebhookNotifier&) = delete;

#ifdef UNIT_TEST
    // Unit-test seam; production code obtains the instance via getInstance().
    explicit WebhookNotifier(const Json::Value& config);
#endif

    // Returns nullptr when the config file defines no enabled webhooks.
    static WebhookNotifier* getInstance();
    static void deleteInstance();

    bool deliverMessage(Json::Value& message) override;
    void retryConnection() override;

    size_t webhookCount() const { return m_webhooks.size(); }

private:
#ifndef UNIT_TEST
    explicit WebhookNotifier(const Json::Value& config);
#endif

    struct RequestConfig
    {
        std::string m_url;
        std::string m_method;
        // Header name -> value: literal text, or "{{event.field}}" to copy a
        // field from the event message.
        std::vector<std::pair<std::string, std::string>> m_headers;
        std::vector<std::pair<std::string, std::string>> m_queryParams;
        long m_timeoutMs{10000};
        int m_maxAttempts{1};
        std::vector<int64_t> m_backoffMs;
        std::vector<int> m_retryOnStatus;  // empty retries any non-2xx status
        // Camera types this receiver accepts; empty receives every matched event.
        std::vector<std::string> m_cameraTypes;
        // Operator-supplied key/value pairs merged into event.metadata of the
        // delivered body; null when the receiver defines none. Ignored when a
        // custom body template is configured.
        Json::Value m_userDefinedMetadata;
        // Validated at load, rendered per delivery as the complete request
        // body; nullopt sends the default tagged notification body.
        std::optional<Json::Value> m_bodyTemplate;
    };

    struct WebhookConfig
    {
        std::string m_id;  // synthesized "<alert_type>/<filter value>" for logging
        std::string m_configId;  // operator-supplied "id", copied to body "webhook_id"
        std::string m_alertType;
        // Event field and value the trigger narrows on, e.g. change == camera_add.
        // An empty field matches every event of the alert type.
        std::string m_filterField;
        std::string m_filterValue;
        std::vector<RequestConfig> m_requests;
    };

    // Travels through AsyncHttpClient's std::any user data: everything needed
    // to judge a completion and resubmit the identical request on failure.
    struct DeliveryState
    {
        size_t m_webhookIndex{0};
        size_t m_requestIndex{0};
        int m_attempt{0};  // 0-based; attempt m_attempt has completed when the callback fires
        std::string m_eventLabel;
        AsyncHttpRequest m_request;
    };

    void loadConfig(const Json::Value& config);
    bool matches(const WebhookConfig& webhook, const Json::Value& message) const;
    AsyncHttpRequest buildRequest(const RequestConfig& requestConfig,
                                  const Json::Value& message,
                                  const std::string& body,
                                  const std::string& webhookId) const;
    static bool shouldRetryResponse(const RequestConfig& requestConfig,
                                    const AsyncHttpResponse& response);
    static int64_t backoffMsForAttempt(const RequestConfig& requestConfig,
                                       int failedAttempt);
    void submitDelivery(DeliveryState state, int64_t delayMs);
    // Runs on the AsyncHttpClient loop thread; must not block. Resubmitting is
    // fine because submit() only enqueues.
    void onDeliveryComplete(const AsyncHttpResponse& response, const std::any& userData);

    std::vector<WebhookConfig> m_webhooks;  // immutable after construction
    std::unique_ptr<AsyncHttpClient> m_httpClient;

    static std::unique_ptr<WebhookNotifier> _instance;
    static std::mutex _instanceMutex;
};
