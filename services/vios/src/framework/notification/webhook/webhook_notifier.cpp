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

#include "webhook_notifier.h"

#include <algorithm>
#include <cctype>
#include <iomanip>
#include <optional>
#include <sstream>

#include "config.h"
#include "logger.h"
#include "utils.h"

namespace
{
constexpr int64_t DEFAULT_RETRY_BACKOFF_MS = 1000;

// Supported alert-type trigger keys and the event field their value narrows on.
struct AlertTrigger
{
    const char* m_alertType;
    const char* m_filterField;
};
constexpr AlertTrigger KNOWN_ALERT_TRIGGERS[] = {
    {"camera_status_change", "change"},
    {"service_status_change", "service_status"},
    {"sensor_metadata", ""},
};

// A header or query parameter value from config is either literal text
// ("application/json") or "{{event.X}}", which copies field X from the
// event block, e.g. "{{event.camera_id}}" for the streamId header. Returns
// std::nullopt for anything else (field missing from the event, or a form we
// do not support yet such as "Bearer {{secrets.token}}") so the caller omits
// the header or parameter entirely.
std::optional<std::string> resolveConfigValue(const std::string& configValue, const Json::Value& message)
{
    if (configValue.find("{{") == std::string::npos)
    {
        return configValue;  // plain literal
    }
    const std::string prefix = "{{event.";
    const std::string suffix = "}}";
    if (configValue.compare(0, prefix.size(), prefix) != 0 ||
        configValue.size() <= prefix.size() + suffix.size() ||
        configValue.compare(configValue.size() - suffix.size(), suffix.size(), suffix) != 0)
    {
        return std::nullopt;
    }
    const std::string field =
        configValue.substr(prefix.size(), configValue.size() - prefix.size() - suffix.size());

    const Json::Value event = message.get("event", Json::nullValue);
    if (!event.isObject() || !event.isMember(field) ||
        !event[field].isConvertibleTo(Json::stringValue))
    {
        return std::nullopt;
    }
    return event[field].asString();
}

std::string urlEncode(const std::string& value)
{
    std::ostringstream encoded;
    encoded << std::hex << std::uppercase << std::setfill('0');
    for (const char c : value)
    {
        const auto uc = static_cast<unsigned char>(c);
        if (std::isalnum(uc) != 0 || c == '-' || c == '_' || c == '.' || c == '~')
        {
            encoded << c;
        }
        else
        {
            encoded << '%' << std::setw(2) << static_cast<int>(uc);
        }
    }
    return encoded.str();
}

std::string jsonFieldAsString(const Json::Value& node, const char* key)
{
    if (!node.isObject())
    {
        return {};
    }
    const Json::Value value = node.get(key, Json::nullValue);
    return value.isConvertibleTo(Json::stringValue) ? value.asString() : std::string();
}

// Identifies the event in logs without reproducing the payload, headers or URLs.
std::string makeEventLabel(const Json::Value& message)
{
    std::string label = "event[" + jsonFieldAsString(message, "alert_type");
    const Json::Value event = message.get("event", Json::nullValue);
    const std::string change = jsonFieldAsString(event, "change");
    if (!change.empty())
    {
        label += "/" + change;
    }
    std::string sensorId = jsonFieldAsString(event, "camera_id");
    if (sensorId.empty())
    {
        sensorId = jsonFieldAsString(message, "id");
    }
    if (!sensorId.empty())
    {
        label += " sensor=" + sensorId;
    }
    return label + "]";
}

// Copies the tagged event body and merges the receiver's user_defined_metadata
// members into event.metadata, creating the object when the event carries
// none. User-defined keys overwrite same-named event-generated keys.
Json::Value mergeUserMetadata(const Json::Value& taggedEvent, const Json::Value& userMetadata)
{
    Json::Value merged = taggedEvent;
    Json::Value& event = merged["event"];
    if (!event.isNull() && !event.isObject())
    {
        // A scalar event cannot hold metadata; operator[] on it would throw.
        event = Json::Value(Json::objectValue);
    }
    Json::Value& metadata = event["metadata"];
    if (!metadata.isNull() && !metadata.isObject())
    {
        metadata = Json::Value(Json::objectValue);
    }
    for (const std::string& key : userMetadata.getMemberNames())
    {
        metadata[key] = userMetadata[key];
    }
    return merged;
}

// The top-level body value is depth level 1: level 32 is valid, level 33 is not.
constexpr int MAX_BODY_TEMPLATE_DEPTH = 32;

// A placeholder path is one or more '.'-separated segments of [A-Za-z0-9_-].
bool isValidPlaceholderPath(const std::string& path)
{
    bool segmentHasChars = false;
    for (const char c : path)
    {
        if (c == '.')
        {
            if (!segmentHasChars)
            {
                return false;  // empty segment: leading or doubled dot
            }
            segmentHasChars = false;
        }
        else if (std::isalnum(static_cast<unsigned char>(c)) != 0 || c == '_' || c == '-')
        {
            segmentHasChars = true;
        }
        else
        {
            return false;
        }
    }
    return segmentHasChars;  // rejects an empty path and a trailing dot
}

// Returns the path when 'text' is exactly "{{path}}" with a valid dotted path.
std::optional<std::string> placeholderPath(const std::string& text)
{
    const std::string open = "{{";
    const std::string close = "}}";
    if (text.size() < open.size() + close.size() + 1 ||
        text.compare(0, open.size(), open) != 0 ||
        text.compare(text.size() - close.size(), close.size(), close) != 0)
    {
        return std::nullopt;
    }
    std::string path = text.substr(open.size(), text.size() - open.size() - close.size());
    if (!isValidPlaceholderPath(path))
    {
        return std::nullopt;  // also rejects "{{a}}x{{b}}": braces are not path characters
    }
    return path;
}

bool containsBraces(const std::string& text)
{
    return text.find("{{") != std::string::npos || text.find("}}") != std::string::npos;
}

// Braces are reserved: a string containing "{{" or "}}" must be exactly a
// placeholder, and property names may not contain them at all. 'error'
// describes the first offending value.
bool validateBodyTemplate(const Json::Value& value, int level, std::string& error)
{
    if (level > MAX_BODY_TEMPLATE_DEPTH)
    {
        error = "value deeper than " + std::to_string(MAX_BODY_TEMPLATE_DEPTH) + " levels";
        return false;
    }
    if (value.isString())
    {
        const std::string text = value.asString();
        if (containsBraces(text) && !placeholderPath(text))
        {
            error = "malformed placeholder '" + text + "'";
            return false;
        }
        return true;
    }
    if (value.isObject())
    {
        for (const std::string& name : value.getMemberNames())
        {
            if (containsBraces(name))
            {
                error = "braces in property name '" + name + "'";
                return false;
            }
            if (!validateBodyTemplate(value[name], level + 1, error))
            {
                return false;
            }
        }
        return true;
    }
    if (value.isArray())
    {
        for (const Json::Value& element : value)
        {
            if (!validateBodyTemplate(element, level + 1, error))
            {
                return false;
            }
        }
        return true;
    }
    return true;  // number, boolean, null
}

// Walks a validated dotted path from the notification root. An absent path,
// including one crossing a non-object, renders as "" and is never an error.
Json::Value lookupNotificationValue(const Json::Value& message, const std::string& path)
{
    const Json::Value* node = &message;
    size_t start = 0;
    while (true)
    {
        const size_t dot = path.find('.', start);
        const std::string segment =
            path.substr(start, dot == std::string::npos ? std::string::npos : dot - start);
        if (!node->isObject() || !node->isMember(segment))
        {
            return Json::Value("");
        }
        node = &(*node)[segment];
        if (dot == std::string::npos)
        {
            return *node;
        }
        start = dot + 1;
    }
}

// Renders a validated template against the raw notification. Type-preserving:
// a looked-up value is copied as-is, so an array stays one array element.
Json::Value renderBodyTemplate(const Json::Value& bodyTemplate, const Json::Value& message)
{
    if (bodyTemplate.isString())
    {
        const std::optional<std::string> path = placeholderPath(bodyTemplate.asString());
        return path ? lookupNotificationValue(message, *path) : bodyTemplate;
    }
    if (bodyTemplate.isObject())
    {
        Json::Value rendered(Json::objectValue);
        for (const std::string& name : bodyTemplate.getMemberNames())
        {
            rendered[name] = renderBodyTemplate(bodyTemplate[name], message);
        }
        return rendered;
    }
    if (bodyTemplate.isArray())
    {
        Json::Value rendered(Json::arrayValue);
        for (const Json::Value& element : bodyTemplate)
        {
            rendered.append(renderBodyTemplate(element, message));
        }
        return rendered;
    }
    return bodyTemplate;
}

bool anyEnabledWebhook(const Json::Value& config)
{
    const Json::Value webhooks = config.get("webhooks", Json::nullValue);
    if (!webhooks.isObject() || !webhooks.get("enabled", false).asBool())
    {
        return false;
    }
    const Json::Value items = webhooks.get("items", Json::nullValue);
    if (!items.isArray())
    {
        return false;
    }
    for (const Json::Value& entry : items)
    {
        if (entry.isObject() && entry.get("enabled", false).asBool())
        {
            return true;
        }
    }
    return false;
}
}  // unnamed namespace

std::unique_ptr<WebhookNotifier> WebhookNotifier::_instance;
std::mutex WebhookNotifier::_instanceMutex;

WebhookNotifier* WebhookNotifier::getInstance()
{
    std::lock_guard<std::mutex> lock(_instanceMutex);
    if (_instance == nullptr)
    {
        std::string parseError;
        const Json::Value config = loadNotificationConfig(NOTIFICATION_CONFIG_FILE, &parseError);
        if (!parseError.empty())
        {
            // Safe to log here: config init has long since finished.
            LOG(error) << "Failed to parse " << NOTIFICATION_CONFIG_FILE << ": " << parseError
                       << "This file is not valid JSON; every webhook after the error is ignored"
                       << endl;
        }
        if (anyEnabledWebhook(config))
        {
            _instance = std::unique_ptr<WebhookNotifier>(new WebhookNotifier(config));
        }
    }
    return _instance.get();
}

void WebhookNotifier::deleteInstance()
{
    std::lock_guard<std::mutex> lock(_instanceMutex);
    _instance.reset();
}

WebhookNotifier::WebhookNotifier(const Json::Value& config)
{
    try
    {
        loadConfig(config);
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Failed to parse webhook config: " << e.what() << endl;
        m_webhooks.clear();
    }
    if (!m_webhooks.empty())
    {
        m_httpClient = std::make_unique<AsyncHttpClient>();
        if (!m_httpClient->start())
        {
            LOG(error) << "Failed to start webhook HTTP client, webhook notifications disabled" << endl;
            m_httpClient.reset();
        }
    }
    // Delivery is per-request HTTP; there is no long-lived connection to lose.
    m_connected = true;
}

WebhookNotifier::~WebhookNotifier()
{
    // Stop the queue worker first so no new submission races the client stop.
    stopMessageProcessing();
    if (m_httpClient != nullptr)
    {
        // Aborted transfers reach onDeliveryComplete with CURLE_ABORTED_BY_CALLBACK,
        // which never resubmits, so the callbacks drain before members are destroyed.
        m_httpClient->stop();
    }
}

void WebhookNotifier::loadConfig(const Json::Value& config)
{
    const Json::Value webhooks = config.get("webhooks", Json::nullValue);
    if (!webhooks.isObject())
    {
        LOG(warning) << "Notification config has no webhooks object, webhook notifications disabled" << endl;
        return;
    }
    if (!webhooks.get("enabled", false).asBool())
    {
        LOG(info) << "Webhooks are globally disabled in notification config" << endl;
        return;
    }
    const Json::Value items = webhooks.get("items", Json::nullValue);
    if (!items.isArray())
    {
        LOG(warning) << "Webhooks config has no items array, webhook notifications disabled" << endl;
        return;
    }
    size_t entryIndex = 0;
    for (const Json::Value& entry : items)
    {
        entryIndex++;
        if (!entry.isObject())
        {
            LOG(error) << "Skipping malformed webhook config entry " << entryIndex << endl;
            continue;
        }
        if (!entry.get("enabled", false).asBool())
        {
            LOG(info) << "Webhook entry " << entryIndex << " is disabled, skipping" << endl;
            continue;
        }

        // The trigger is a supported alert-type key: "<alert_type>": "<filter value>".
        WebhookConfig webhook;
        std::string filterValue;
        std::string filterField;
        for (const AlertTrigger& trigger : KNOWN_ALERT_TRIGGERS)
        {
            if (entry.isMember(trigger.m_alertType))
            {
                webhook.m_alertType = trigger.m_alertType;
                filterValue = jsonFieldAsString(entry, trigger.m_alertType);
                filterField = trigger.m_filterField;
                break;
            }
        }
        if (webhook.m_alertType.empty())
        {
            LOG(error) << "Webhook entry " << entryIndex << " has no supported alert type key, skipping" << endl;
            continue;
        }
        webhook.m_configId = jsonFieldAsString(entry, "id");
        webhook.m_id = webhook.m_alertType + (filterValue.empty() ? "" : "/" + filterValue);
        if (!webhook.m_configId.empty())
        {
            webhook.m_id += " (" + webhook.m_configId + ")";
        }
        if (!filterValue.empty())
        {
            if (filterField.empty())
            {
                LOG(warning) << "Webhook " << webhook.m_id << ": alert type '" << webhook.m_alertType
                             << "' has no filter field, matching every event of that type" << endl;
            }
            else
            {
                webhook.m_filterField = filterField;
                webhook.m_filterValue = filterValue;
            }
        }

        const Json::Value requests = entry.get("request", Json::nullValue);
        if (!requests.isArray())
        {
            LOG(error) << "Webhook " << webhook.m_id << ": request must be an array, skipping" << endl;
            continue;
        }
        // 1-based position in the configured request array: unlike the count of
        // already-loaded receivers, this still names an entry that is skipped.
        size_t requestPosition = 0;
        for (const Json::Value& requestJson : requests)
        {
            requestPosition++;
            if (!requestJson.isObject())
            {
                LOG(error) << "Webhook " << webhook.m_id << ": skipping malformed receiver entry request["
                           << requestPosition << "]" << endl;
                continue;
            }
            RequestConfig requestConfig;
            requestConfig.m_url = jsonFieldAsString(requestJson, "url");
            if (requestConfig.m_url.empty())
            {
                LOG(error) << "Webhook " << webhook.m_id << ": receiver "
                           << (webhook.m_requests.size() + 1) << " has no url, skipped" << endl;
                continue;
            }
            requestConfig.m_method = jsonFieldAsString(requestJson, "method");
            if (requestConfig.m_method.empty())
            {
                LOG(error) << "Webhook " << webhook.m_id << ": receiver "
                           << (webhook.m_requests.size() + 1) << " has no method, skipped" << endl;
                continue;
            }
            const Json::Value headers = requestJson.get("headers", Json::nullValue);
            if (headers.isObject())
            {
                for (const std::string& name : headers.getMemberNames())
                {
                    requestConfig.m_headers.emplace_back(name, jsonFieldAsString(headers, name.c_str()));
                }
            }
            const Json::Value queryParams = requestJson.get("query_params", Json::nullValue);
            if (queryParams.isObject())
            {
                for (const std::string& name : queryParams.getMemberNames())
                {
                    requestConfig.m_queryParams.emplace_back(name,
                                                             jsonFieldAsString(queryParams, name.c_str()));
                }
            }
            const Json::Value cameraTypes = requestJson.get("camera_type", Json::nullValue);
            if (cameraTypes.isArray())
            {
                for (const Json::Value& cameraType : cameraTypes)
                {
                    if (cameraType.isString() && !cameraType.asString().empty())
                    {
                        requestConfig.m_cameraTypes.push_back(cameraType.asString());
                    }
                    else
                    {
                        LOG(error) << "Webhook " << webhook.m_id << ": receiver "
                                   << (webhook.m_requests.size() + 1)
                                   << " has a non-string camera_type entry, ignored" << endl;
                    }
                }
            }
            else if (!cameraTypes.isNull())
            {
                LOG(error) << "Webhook " << webhook.m_id << ": receiver "
                           << (webhook.m_requests.size() + 1)
                           << " camera_type must be an array, filter ignored" << endl;
            }
            const Json::Value userMetadata = requestJson.get("user_defined_metadata", Json::nullValue);
            if (userMetadata.isObject())
            {
                requestConfig.m_userDefinedMetadata = userMetadata;
            }
            else if (!userMetadata.isNull())
            {
                LOG(error) << "Webhook " << webhook.m_id << ": receiver "
                           << (webhook.m_requests.size() + 1)
                           << " user_defined_metadata must be a JSON object, ignored" << endl;
            }
            // Presence, not emptiness, selects custom rendering: body {} sends {}.
            if (requestJson.isMember("body"))
            {
                std::string templateError;
                if (!validateBodyTemplate(requestJson["body"], 1, templateError))
                {
                    LOG(error) << "Webhook " << webhook.m_id << ": receiver request["
                               << requestPosition << "] (" << requestConfig.m_url
                               << ") has an invalid body template (" << templateError
                               << "), skipped" << endl;
                    continue;
                }
                if (!requestConfig.m_userDefinedMetadata.isNull())
                {
                    LOG(warning) << "Webhook " << webhook.m_id << ": receiver request["
                                 << requestPosition << "] (" << requestConfig.m_url
                                 << ") configures both body and user_defined_metadata; the custom"
                                 << " body is the complete request body and user_defined_metadata"
                                 << " is ignored" << endl;
                    requestConfig.m_userDefinedMetadata = Json::nullValue;
                }
                requestConfig.m_bodyTemplate = requestJson["body"];
            }
            const Json::Value timeoutMs = requestJson.get("timeout_ms", Json::nullValue);
            if (timeoutMs.isNumeric())
            {
                requestConfig.m_timeoutMs = timeoutMs.asInt();
            }

            const Json::Value retry = requestJson.get("retry", Json::nullValue);
            if (retry.isObject())
            {
                const Json::Value maxAttempts = retry.get("max_attempts", Json::nullValue);
                if (maxAttempts.isNumeric())
                {
                    requestConfig.m_maxAttempts = std::max(1, maxAttempts.asInt());
                }
                const Json::Value backoffList = retry.get("backoff_ms", Json::nullValue);
                if (backoffList.isArray())
                {
                    for (const Json::Value& backoff : backoffList)
                    {
                        if (backoff.isNumeric())
                        {
                            requestConfig.m_backoffMs.push_back(backoff.asInt64());
                        }
                    }
                }
                const Json::Value retryOnStatus = retry.get("retry_on_status", Json::nullValue);
                if (retryOnStatus.isArray())
                {
                    for (const Json::Value& status : retryOnStatus)
                    {
                        if (status.isNumeric())
                        {
                            requestConfig.m_retryOnStatus.push_back(status.asInt());
                        }
                    }
                }
            }
            webhook.m_requests.push_back(std::move(requestConfig));
        }
        if (webhook.m_requests.empty())
        {
            LOG(error) << "Webhook " << webhook.m_id << ": no valid receivers, skipping" << endl;
            continue;
        }

        const std::string authType = jsonFieldAsString(entry.get("auth", Json::nullValue), "type");
        if (!authType.empty())
        {
            LOG(warning) << "Webhook " << webhook.m_id << ": auth type '" << authType
                         << "' is not supported yet, requests are sent unsigned" << endl;
        }

        LOG(info) << "Webhook " << webhook.m_id << " enabled with " << webhook.m_requests.size()
                  << " receiver(s)" << endl;
        m_webhooks.push_back(std::move(webhook));
    }
    LOG(info) << "Loaded " << m_webhooks.size() << " enabled webhook(s)" << endl;
}

bool WebhookNotifier::matches(const WebhookConfig& webhook, const Json::Value& message) const
{
    if (jsonFieldAsString(message, "alert_type") != webhook.m_alertType)
    {
        return false;
    }
    if (webhook.m_filterField.empty())
    {
        return true;  // no filter: every event of this alert type matches
    }
    const Json::Value event = message.get("event", Json::nullValue);
    return jsonFieldAsString(event, webhook.m_filterField.c_str()) == webhook.m_filterValue;
}

AsyncHttpRequest WebhookNotifier::buildRequest(const RequestConfig& requestConfig,
                                               const Json::Value& message,
                                               const std::string& body,
                                               const std::string& webhookId) const
{
    AsyncHttpRequest request;
    request.m_url = requestConfig.m_url;
    request.m_method = requestConfig.m_method;
    request.m_body = body;
    request.m_timeoutMs = requestConfig.m_timeoutMs;

    for (const auto& [name, configValue] : requestConfig.m_headers)
    {
        std::optional<std::string> value = resolveConfigValue(configValue, message);
        if (!value)
        {
            // Typically {{secrets.*}}: a broken credential header would be
            // worse than no header at all.
            LOG(verbose) << "Webhook " << webhookId << ": header '" << name
                         << "' has an unresolved placeholder, omitted" << endl;
            continue;
        }
        // Event fields can carry user-supplied text (e.g. sensor names);
        // strip CR and LF so a crafted value cannot inject extra headers.
        value->erase(std::remove_if(value->begin(), value->end(),
                                    [](char c) { return c == '\r' || c == '\n'; }),
                     value->end());
        request.m_headers.push_back(name + ": " + *value);
    }

    std::string query;
    for (const auto& [name, configValue] : requestConfig.m_queryParams)
    {
        const std::optional<std::string> value = resolveConfigValue(configValue, message);
        if (!value)
        {
            LOG(verbose) << "Webhook " << webhookId << ": query parameter '" << name
                         << "' has an unresolved placeholder, omitted" << endl;
            continue;
        }
        query += (query.empty() ? "" : "&") + urlEncode(name) + "=" + urlEncode(*value);
    }
    if (!query.empty())
    {
        request.m_url += (request.m_url.find('?') == std::string::npos ? "?" : "&") + query;
    }
    return request;
}

bool WebhookNotifier::shouldRetryResponse(const RequestConfig& requestConfig,
                                          const AsyncHttpResponse& response)
{
    if (!response.transportOk())
    {
        return true;
    }
    if (requestConfig.m_retryOnStatus.empty())
    {
        return true;
    }
    return std::find(requestConfig.m_retryOnStatus.begin(), requestConfig.m_retryOnStatus.end(),
                     static_cast<int>(response.m_httpStatus)) != requestConfig.m_retryOnStatus.end();
}

int64_t WebhookNotifier::backoffMsForAttempt(const RequestConfig& requestConfig, int failedAttempt)
{
    if (requestConfig.m_backoffMs.empty())
    {
        return DEFAULT_RETRY_BACKOFF_MS;
    }
    const auto index =
        std::min(static_cast<size_t>(failedAttempt), requestConfig.m_backoffMs.size() - 1);
    return requestConfig.m_backoffMs[index];
}

bool WebhookNotifier::deliverMessage(Json::Value& message)
{
    // Always returns true: delivery is asynchronous from here on and retries
    // are handled per receiver in onDeliveryComplete, not by the base queue.
    if (m_webhooks.empty())
    {
        return true;
    }
    const Json::Value& event = message;
    const std::string loggingLabel = makeEventLabel(event);
    if (m_httpClient == nullptr || !m_httpClient->isRunning())
    {
        LOG(error) << "Webhook HTTP client not running, dropping " << loggingLabel << endl;
        return true;
    }
    const std::string cameraType = jsonFieldAsString(event.get("event", Json::nullValue), "camera_type");

    size_t matched = 0;
    for (size_t i = 0; i < m_webhooks.size(); i++)
    {
        const WebhookConfig& webhook = m_webhooks[i];
        if (!matches(webhook, event))
        {
            continue;
        }
        matched++;
        // Tag the body with this webhook's id (empty when unconfigured).
        Json::Value taggedEvent = event;
        taggedEvent["webhook_id"] = webhook.m_configId;
        const std::string body = jsonToString(taggedEvent);
        for (size_t r = 0; r < webhook.m_requests.size(); r++)
        {
            const RequestConfig& requestConfig = webhook.m_requests[r];
            if (!requestConfig.m_cameraTypes.empty() &&
                std::find(requestConfig.m_cameraTypes.begin(), requestConfig.m_cameraTypes.end(),
                          cameraType) == requestConfig.m_cameraTypes.end())
            {
                LOG(info) << "Webhook " << webhook.m_id << ": receiver " << (r + 1)
                          << " skipped, camera_type '" << cameraType << "' not in its filter" << endl;
                continue;
            }
            LOG(info) << "Webhook " << webhook.m_id << ": delivering " << loggingLabel << " to receiver "
                      << (r + 1) << "/" << webhook.m_requests.size() << " (attempt 1/"
                      << requestConfig.m_maxAttempts << ")" << endl;
            // A custom body renders from the raw event, not taggedEvent: the
            // template is the complete body, so webhook_id is not added.
            std::string receiverBody;
            if (requestConfig.m_bodyTemplate)
            {
                receiverBody = jsonToString(renderBodyTemplate(*requestConfig.m_bodyTemplate, event));
            }
            else if (requestConfig.m_userDefinedMetadata.isNull())
            {
                receiverBody = body;
            }
            else
            {
                receiverBody =
                    jsonToString(mergeUserMetadata(taggedEvent, requestConfig.m_userDefinedMetadata));
            }
            DeliveryState state;
            state.m_webhookIndex = i;
            state.m_requestIndex = r;
            state.m_attempt = 0;
            state.m_eventLabel = loggingLabel;
            state.m_request = buildRequest(requestConfig, event, receiverBody, webhook.m_id);
            submitDelivery(std::move(state), 0);
        }
    }
    if (matched == 0)
    {
        LOG(info) << "No webhook matched " << loggingLabel << endl;
    }
    return true;
}

void WebhookNotifier::retryConnection()
{
    // Nothing to reconnect: each delivery is an independent HTTP request and
    // m_connected stays true from construction.
}

void WebhookNotifier::submitDelivery(DeliveryState state, int64_t delayMs)
{
    const std::string webhookId = m_webhooks[state.m_webhookIndex].m_id;
    const std::string eventLabel = state.m_eventLabel;
    AsyncHttpRequest request = state.m_request;
    const bool submitted = m_httpClient->submit(
        std::move(request),
        [this](const AsyncHttpResponse& response, const std::any& userData) {
            onDeliveryComplete(response, userData);
        },
        std::move(state), delayMs);
    if (!submitted)
    {
        LOG(error) << "Webhook " << webhookId << ": failed to enqueue " << eventLabel << endl;
    }
}

void WebhookNotifier::onDeliveryComplete(const AsyncHttpResponse& response, const std::any& userData)
{
    const DeliveryState* state = std::any_cast<DeliveryState>(&userData);
    if (state == nullptr || state->m_webhookIndex >= m_webhooks.size() ||
        state->m_requestIndex >= m_webhooks[state->m_webhookIndex].m_requests.size())
    {
        LOG(error) << "Webhook completion carries no valid delivery state" << endl;
        return;
    }
    const WebhookConfig& webhook = m_webhooks[state->m_webhookIndex];
    const RequestConfig& requestConfig = webhook.m_requests[state->m_requestIndex];
    const std::string receiver = "receiver " + std::to_string(state->m_requestIndex + 1) + "/" +
                                 std::to_string(webhook.m_requests.size());
    const int attemptNumber = state->m_attempt + 1;

    if (response.transportOk() && response.m_httpStatus >= 200 && response.m_httpStatus < 300)
    {
        LOG(info) << "Webhook " << webhook.m_id << ": delivered " << state->m_eventLabel << " to "
                  << receiver << ", HTTP " << response.m_httpStatus << " (attempt " << attemptNumber
                  << "/" << requestConfig.m_maxAttempts << ")" << endl;
        return;
    }

    if (response.m_curlCode == CURLE_ABORTED_BY_CALLBACK)
    {
        // The client is shutting down; a resubmit would be rejected anyway.
        LOG(warning) << "Webhook " << webhook.m_id << ": delivery of " << state->m_eventLabel
                     << " to " << receiver << " aborted by shutdown" << endl;
        return;
    }

    const std::string failure = response.transportOk()
        ? "unexpected HTTP " + std::to_string(response.m_httpStatus)
        : "transport error: " + response.m_error;

    if (!shouldRetryResponse(requestConfig, response))
    {
        LOG(error) << "Webhook " << webhook.m_id << ": " << failure << " for " << state->m_eventLabel
                   << " to " << receiver << " is not retryable, giving up" << endl;
        return;
    }

    if (attemptNumber >= requestConfig.m_maxAttempts)
    {
        LOG(error) << "Webhook " << webhook.m_id << ": giving up on " << state->m_eventLabel
                   << " to " << receiver << " after attempt " << attemptNumber << "/"
                   << requestConfig.m_maxAttempts << ": " << failure << endl;
        return;
    }

    const int64_t backoffMs = backoffMsForAttempt(requestConfig, state->m_attempt);
    LOG(warning) << "Webhook " << webhook.m_id << ": " << failure << " for " << state->m_eventLabel
                 << " to " << receiver << " (attempt " << attemptNumber << "/"
                 << requestConfig.m_maxAttempts << "), retrying in " << backoffMs << " ms" << endl;

    DeliveryState next = *state;
    next.m_attempt++;
    submitDelivery(std::move(next), backoffMs);
}
