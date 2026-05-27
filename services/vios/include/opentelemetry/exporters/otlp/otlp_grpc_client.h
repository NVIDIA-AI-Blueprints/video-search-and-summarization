// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <grpcpp/grpcpp.h>

#include <memory>

#include "opentelemetry/exporters/otlp/otlp_grpc_exporter_options.h"

#include "opentelemetry/exporters/otlp/protobuf_include_prefix.h"

#include "opentelemetry/proto/collector/logs/v1/logs_service.grpc.pb.h"
#include "opentelemetry/proto/collector/metrics/v1/metrics_service.grpc.pb.h"
#include "opentelemetry/proto/collector/trace/v1/trace_service.grpc.pb.h"

#include "opentelemetry/exporters/otlp/protobuf_include_suffix.h"

OPENTELEMETRY_BEGIN_NAMESPACE
namespace exporter
{
namespace otlp
{

/**
 * The OTLP gRPC client contains utility functions of gRPC.
 */
class OtlpGrpcClient
{
public:
  /**
   * Create gRPC channel from the exporter options.
   */
  static std::shared_ptr<grpc::Channel> MakeChannel(const OtlpGrpcExporterOptions &options);

  /**
   * Create gRPC client context to call RPC.
   */
  static std::unique_ptr<grpc::ClientContext> MakeClientContext(
      const OtlpGrpcExporterOptions &options);

  /**
   * Create gRPC CompletionQueue to async call RPC.
   */
  static std::unique_ptr<grpc::CompletionQueue> MakeCompletionQueue();

  /**
   * Create trace service stub to communicate with the OpenTelemetry Collector.
   */
  static std::unique_ptr<proto::collector::trace::v1::TraceService::StubInterface>
  MakeTraceServiceStub(const OtlpGrpcExporterOptions &options);

  /**
   * Create metrics service stub to communicate with the OpenTelemetry Collector.
   */
  static std::unique_ptr<proto::collector::metrics::v1::MetricsService::StubInterface>
  MakeMetricsServiceStub(const OtlpGrpcExporterOptions &options);

  /**
   * Create logs service stub to communicate with the OpenTelemetry Collector.
   */
  static std::unique_ptr<proto::collector::logs::v1::LogsService::StubInterface>
  MakeLogsServiceStub(const OtlpGrpcExporterOptions &options);

  static grpc::Status DelegateExport(
      proto::collector::trace::v1::TraceService::StubInterface *stub,
      grpc::ClientContext *context,
      const proto::collector::trace::v1::ExportTraceServiceRequest &request,
      proto::collector::trace::v1::ExportTraceServiceResponse *response);

  static grpc::Status DelegateExport(
      proto::collector::metrics::v1::MetricsService::StubInterface *stub,
      grpc::ClientContext *context,
      const proto::collector::metrics::v1::ExportMetricsServiceRequest &request,
      proto::collector::metrics::v1::ExportMetricsServiceResponse *response);

  static grpc::Status DelegateExport(
      proto::collector::logs::v1::LogsService::StubInterface *stub,
      grpc::ClientContext *context,
      const proto::collector::logs::v1::ExportLogsServiceRequest &request,
      proto::collector::logs::v1::ExportLogsServiceResponse *response);
};
}  // namespace otlp
}  // namespace exporter
OPENTELEMETRY_END_NAMESPACE
