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

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.propagate import get_global_textmap, set_global_textmap, inject
from opentelemetry import baggage
from opentelemetry import propagate
from opentelemetry.trace import Status, StatusCode


import json
import os
import logging
import requests

logger = logging.getLogger(__name__)
# Do not call logging.basicConfig here — app/run_workloads configure root logging
# via lib.logging.configure_root_logging (level/format/handlers).

# OTEL Configs
REDIS_OTEL_CONTEXT_HASHMAP = "trace_contexts"
otel_service_name = os.environ.get("OTEL_SERVICE_NAME", "SDR_AGENT")
resource = Resource.create({SERVICE_NAME: otel_service_name})
provider = TracerProvider(resource=resource)
processor = SimpleSpanProcessor(OTLPSpanExporter())
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(otel_service_name)

propagator = TraceContextTextMapPropagator()

set_global_textmap(propagator)

# Inject the context
def inject_context(ctx):
    carrier = {}
    # get_global_textmap().inject(carrier, context=ctx)
    inject(carrier, context=ctx)
    return carrier

# Retrieve the context from Redis for the given stream ID.
def retrieve_context(stream_id, redisMsging):
    try:
        ctx = None
        redis_client = redisMsging.getRedisConnection()
        if redis_client.hexists(REDIS_OTEL_CONTEXT_HASHMAP, stream_id):
            otel_context = redis_client.hget(REDIS_OTEL_CONTEXT_HASHMAP, stream_id)
            # Parse the JSON string
            parsed_context = json.loads(json.loads(otel_context))
            # Create a carrier with the traceparent
            carrier = {'traceparent': parsed_context.get("traceparent")}
            ctx = TraceContextTextMapPropagator().extract(carrier=carrier)

    except Exception as e:
        logger.exception(f"Unable to retrieve Otel context from Redis.")
        
    finally:
        return ctx
      
# Store the context in Redis under the given stream ID.
def propagate_context(stream_id, redisMsging, otel_context, sdr_type):
    try:
        redis_client = redisMsging.getRedisConnection()
        # output = {}
        logger.info(f"otel_context={otel_context}")
        # carrier = {}
        # get_global_textmap().inject(carrier, context=ctx)
        # inject(carrier, context=otel_context)
        carrier = inject_context(otel_context)
        logger.info(f"final_ctx={carrier}")
        logger.info(f"baggage.get_all()={baggage.get_all(context=otel_context)}")
        # inject(output, otel_context)
        
        redis_client.hset(f"trace_contexts_{sdr_type}", stream_id, json.dumps(json.dumps(carrier)))
    except Exception as e:
        logger.exception(f"Unable to propagate Otel context of {sdr_type} to Redis.")
        
# Delete the context entry from Redis for the given stream ID.
def delete_context_entry(stream_id, redisMsging, sdr_type):
    try:
        redis_client = redisMsging.getRedisConnection()
        redis_client.hdel(f"trace_contexts_{sdr_type}", stream_id)
    except Exception as e:
        logger.exception(f"Unable to delete context entry {stream_id} from \"trace_contexts_{sdr_type}\.")
    
def create_parent_span(span_name, function, redisMsging):
    parent_context = retrieve_context(span_name, redisMsging)
    span = tracer.start_span(span_name, context=parent_context)
    span.set_attribute("stream", span_name)
    span.set_attribute("function", function)
    current_context = trace.set_span_in_context(span)
    return span, current_context

def create_child_span(type, video_name, podInfoItm, configData, parent_context, conf):
    if type == "add":
        span_name = "provision"
    elif type == "remove":
        span_name = "deprovision"
    elif type == "vst_streams":
        span_name = "VST Streams fetched"
    else:
        return None
    span = tracer.start_span(span_name, context=parent_context)
    if type == "add" or type == "remove":
        span.set_attribute("podInfoItem", str(podInfoItm))
        span.set_attribute("configData", str(configData))
    else:
        span.set_attribute("streams", str(configData))
    current_context = trace.set_span_in_context(span)
    stream_id = configData[conf["WDM_EVENT_OBJECT_FIELD"]][conf["WDM_WL_ID_FIELD"]]
    ctx_with_baggage = baggage.set_baggage("stream_id", str(stream_id), context=current_context)
    return span, ctx_with_baggage

