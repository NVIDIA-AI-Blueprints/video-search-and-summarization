// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "opentelemetry/exporters/otlp/otlp_grpc_exporter_options.h"
#include "opentelemetry/exporters/otlp/otlp_preferred_temporality.h"
#include "opentelemetry/sdk/metrics/instruments.h"

#include <memory>

OPENTELEMETRY_BEGIN_NAMESPACE
namespace exporter
{
namespace otlp
{

/**
 * Struct to hold OTLP GRPC metrics exporter options.
 */
struct OtlpGrpcMetricExporterOptions : public OtlpGrpcExporterOptions
{

  // Preferred Aggregation Temporality
  PreferredAggregationTemporality aggregation_temporality =
      PreferredAggregationTemporality::kCumulative;
};

}  // namespace otlp
}  // namespace exporter
OPENTELEMETRY_END_NAMESPACE
