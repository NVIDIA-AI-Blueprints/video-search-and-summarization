// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "opentelemetry/exporters/otlp/otlp_grpc_exporter_options.h"
#include "opentelemetry/sdk/logs/exporter.h"

OPENTELEMETRY_BEGIN_NAMESPACE
namespace exporter
{
namespace otlp
{

/**
 * Factory class for OtlpGrpcLogRecordExporter.
 */
class OPENTELEMETRY_EXPORT OtlpGrpcLogRecordExporterFactory
{
public:
  /**
   * Create a OtlpGrpcLogRecordExporter.
   */
  static std::unique_ptr<opentelemetry::sdk::logs::LogRecordExporter> Create();

  /**
   * Create a OtlpGrpcLogRecordExporter.
   */
  static std::unique_ptr<opentelemetry::sdk::logs::LogRecordExporter> Create(
      const OtlpGrpcExporterOptions &options);
};

}  // namespace otlp
}  // namespace exporter
OPENTELEMETRY_END_NAMESPACE
