// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "opentelemetry/exporters/otlp/otlp_environment.h"
#include "opentelemetry/exporters/otlp/otlp_http.h"
#include "opentelemetry/sdk/logs/exporter.h"

#include <chrono>
#include <cstddef>
#include <memory>
#include <string>

OPENTELEMETRY_BEGIN_NAMESPACE
namespace exporter
{
namespace otlp
{

/**
 * Struct to hold OTLP HTTP logs exporter options.
 */
struct OtlpHttpLogRecordExporterOptions
{
  // The endpoint to export to. By default
  // @see
  // https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/protocol/otlp.md
  // @see https://github.com/open-telemetry/opentelemetry-collector/tree/main/receiver/otlpreceiver
  std::string url = GetOtlpDefaultHttpLogsEndpoint();

  // By default, post json data
  HttpRequestContentType content_type = HttpRequestContentType::kJson;

  // If convert bytes into hex. By default, we will convert all bytes but id into base64
  // This option is ignored if content_type is not kJson
  JsonBytesMappingKind json_bytes_mapping = JsonBytesMappingKind::kHexId;

  // If using the json name of protobuf field to set the key of json. By default, we will use the
  // field name just like proto files.
  bool use_json_name = false;

  // Whether to print the status of the exporter in the console
  bool console_debug = false;

  std::chrono::system_clock::duration timeout = GetOtlpDefaultLogsTimeout();

  // Additional HTTP headers
  OtlpHeaders http_headers = GetOtlpDefaultLogsHeaders();

#ifdef ENABLE_ASYNC_EXPORT
  // Concurrent requests
  // https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/protocol/otlp.md#otlpgrpc-concurrent-requests
  std::size_t max_concurrent_requests = 64;

  // Requests per connections
  std::size_t max_requests_per_connection = 8;
#endif

#ifdef ENABLE_OTLP_HTTP_SSL_PREVIEW
  bool ssl_insecure_skip_verify{false};

  std::string ssl_ca_cert_path   = GetOtlpDefaultLogsSslCertificatePath();
  std::string ssl_ca_cert_string = GetOtlpDefaultLogsSslCertificateString();

  std::string ssl_client_key_path   = GetOtlpDefaultLogsSslClientKeyPath();
  std::string ssl_client_key_string = GetOtlpDefaultLogsSslClientKeyString();

  std::string ssl_client_cert_path   = GetOtlpDefaultLogsSslClientCertificatePath();
  std::string ssl_client_cert_string = GetOtlpDefaultLogsSslClientCertificateString();
#endif /* ENABLE_OTLP_HTTP_SSL_PREVIEW */

#ifdef ENABLE_OTLP_HTTP_SSL_TLS_PREVIEW
  /** Minimum TLS version. */
  std::string ssl_min_tls = GetOtlpDefaultLogsSslTlsMinVersion();
  /** Maximum TLS version. */
  std::string ssl_max_tls = GetOtlpDefaultLogsSslTlsMaxVersion();
  /** TLS cipher. */
  std::string ssl_cipher = GetOtlpDefaultLogsSslTlsCipher();
  /** TLS cipher suite. */
  std::string ssl_cipher_suite = GetOtlpDefaultLogsSslTlsCipherSuite();
#endif /* ENABLE_OTLP_HTTP_SSL_TLS_PREVIEW */
};

}  // namespace otlp
}  // namespace exporter
OPENTELEMETRY_END_NAMESPACE
