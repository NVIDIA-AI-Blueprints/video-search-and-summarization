/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "OtelTracing.h"

#ifdef USE_OTEL
#include "opentelemetry/exporters/otlp/otlp_http_exporter_factory.h"
#include "opentelemetry/sdk/trace/tracer_provider_factory.h"
#include "opentelemetry/sdk/trace/batch_span_processor_factory.h"
#include "opentelemetry/sdk/trace/batch_span_processor_options.h"
#include "opentelemetry/sdk/trace/processor.h"
#include "opentelemetry/sdk/resource/resource.h"
#include "opentelemetry/trace/provider.h"
#include <atomic>

namespace trace_api = opentelemetry::trace;
namespace trace_sdk = opentelemetry::sdk::trace;
namespace otlp = opentelemetry::exporter::otlp;
namespace resource = opentelemetry::sdk::resource;
namespace nostd = opentelemetry::nostd;

std::shared_ptr<trace_api::TracerProvider> OtelTracing::s_provider;
nostd::shared_ptr<trace_api::Tracer> OtelTracing::s_tracer;
std::atomic<bool> OtelTracing::s_initialized{false};
#endif

bool OtelTracing::s_enabled = false;

void OtelTracing::Initialize(const std::string& otlp_endpoint, const std::string& service_name)
{
#ifdef USE_OTEL
    // Prevent double initialization
    bool expected = false;
    if (!s_initialized.compare_exchange_strong(expected, true))
    {
        return;  // Already initialized
    }
    
    try
    {
        otlp::OtlpHttpExporterOptions exporter_options;
        exporter_options.url = otlp_endpoint;
        
        auto exporter = otlp::OtlpHttpExporterFactory::Create(exporter_options);
        
        trace_sdk::BatchSpanProcessorOptions processor_options;
        auto processor = trace_sdk::BatchSpanProcessorFactory::Create(std::move(exporter), processor_options);
        
        resource::ResourceAttributes attributes = {
            {"service.name", service_name},
            {"service.version", "1.0.0"}
        };
        auto resource_ptr = resource::Resource::Create(attributes);
        
        s_provider = trace_sdk::TracerProviderFactory::Create(std::move(processor), resource_ptr);
        trace_api::Provider::SetTracerProvider(s_provider);
        
        s_tracer = s_provider->GetTracer(service_name, "1.0.0");
        s_enabled = true;
    }
    catch (const std::exception& e)
    {
        s_enabled = false;
        s_initialized.store(false);  // Allow retry on failure
    }
#else
    (void)otlp_endpoint;
    (void)service_name;
    s_enabled = false;
#endif
}

void OtelTracing::Shutdown()
{
#ifdef USE_OTEL
    // Prevent double shutdown
    bool expected = true;
    if (!s_initialized.compare_exchange_strong(expected, false))
    {
        return;  // Already shut down or not initialized
    }
    
    s_enabled = false;
    s_tracer = nullptr;
    
    // Clear global provider first to release reference
    trace_api::Provider::SetTracerProvider(std::shared_ptr<trace_api::TracerProvider>(nullptr));
    
    // Now reset our reference (should trigger actual destruction)
    if (s_provider)
    {
        s_provider.reset();
    }
#else
    s_enabled = false;
#endif
}

#ifdef USE_OTEL
trace_api::Tracer* OtelTracing::GetTracer()
{
    if (s_tracer)
    {
        return s_tracer.get();
    }
    return nullptr;
}
#else
void* OtelTracing::GetTracer()
{
    return nullptr;
}
#endif

bool OtelTracing::IsEnabled()
{
#ifdef USE_OTEL
    return s_enabled && s_tracer != nullptr;
#else
    return false;
#endif
}

