// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "opentelemetry/exporters/otlp/otlp_environment.h"

#include <memory>

OPENTELEMETRY_BEGIN_NAMESPACE
namespace exporter
{
namespace otlp
{

/**
 * Struct to hold OTLP GRPC traces exporter options.
 */
struct OtlpGrpcExporterOptions
{
  // The endpoint to export to. By default the OpenTelemetry Collector's default endpoint.
  std::string endpoint = GetOtlpDefaultGrpcEndpoint();
  // By default when false, uses grpc::InsecureChannelCredentials(); If true,
  // uses ssl_credentials_cacert_path if non-empty, else uses ssl_credentials_cacert_as_string
  bool use_ssl_credentials = GetOtlpDefaultIsSslEnable();
  // ssl_credentials_cacert_path specifies path to .pem file to be used for SSL encryption.
  std::string ssl_credentials_cacert_path = GetOtlpDefaultSslCertificatePath();
  // ssl_credentials_cacert_as_string in-memory string representation of .pem file to be used for
  // SSL encryption.
  std::string ssl_credentials_cacert_as_string = GetOtlpDefaultSslCertificateString();

#ifdef ENABLE_OTLP_GRPC_SSL_MTLS_PREVIEW
  // At most one of ssl_client_key_* should be non-empty. If use_ssl_credentials, they will
  // be read to allow for mTLS.
  std::string ssl_client_key_path   = GetOtlpDefaultTracesSslClientKeyPath();
  std::string ssl_client_key_string = GetOtlpDefaultTracesSslClientKeyString();

  // At most one of ssl_client_cert_* should be non-empty. If use_ssl_credentials, they will
  // be read to allow for mTLS.
  std::string ssl_client_cert_path   = GetOtlpDefaultTracesSslClientCertificatePath();
  std::string ssl_client_cert_string = GetOtlpDefaultTracesSslClientCertificateString();
#endif

  // Timeout for grpc deadline
  std::chrono::system_clock::duration timeout = GetOtlpDefaultTimeout();
  // Additional HTTP headers
  OtlpHeaders metadata = GetOtlpDefaultHeaders();
  // User agent
  std::string user_agent = GetOtlpDefaultUserAgent();
};

}  // namespace otlp
}  // namespace exporter
OPENTELEMETRY_END_NAMESPACE
