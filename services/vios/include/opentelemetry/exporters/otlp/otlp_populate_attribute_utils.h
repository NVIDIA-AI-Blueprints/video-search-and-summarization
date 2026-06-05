// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

#pragma once

// clang-format off
#include "opentelemetry/exporters/otlp/protobuf_include_prefix.h"
#include "opentelemetry/proto/resource/v1/resource.pb.h"
#include "opentelemetry/exporters/otlp/protobuf_include_suffix.h"
// clang-format on

#include "opentelemetry/common/attribute_value.h"
#include "opentelemetry/nostd/string_view.h"
#include "opentelemetry/sdk/common/attribute_utils.h"
#include "opentelemetry/sdk/resource/resource.h"
#include "opentelemetry/version.h"

OPENTELEMETRY_BEGIN_NAMESPACE
namespace exporter
{
namespace otlp
{
/**
 * The OtlpCommoneUtils contains utility functions to populate attributes
 */
class OtlpPopulateAttributeUtils
{

public:
  static void PopulateAttribute(opentelemetry::proto::resource::v1::Resource *proto,
                                const opentelemetry::sdk::resource::Resource &resource) noexcept;

  static void PopulateAnyValue(opentelemetry::proto::common::v1::AnyValue *proto_value,
                               const opentelemetry::common::AttributeValue &value) noexcept;

  static void PopulateAnyValue(
      opentelemetry::proto::common::v1::AnyValue *proto_value,
      const opentelemetry::sdk::common::OwnedAttributeValue &value) noexcept;

  static void PopulateAttribute(opentelemetry::proto::common::v1::KeyValue *attribute,
                                nostd::string_view key,
                                const opentelemetry::common::AttributeValue &value) noexcept;

  static void PopulateAttribute(
      opentelemetry::proto::common::v1::KeyValue *attribute,
      nostd::string_view key,
      const opentelemetry::sdk::common::OwnedAttributeValue &value) noexcept;
};

}  // namespace otlp
}  // namespace exporter
OPENTELEMETRY_END_NAMESPACE
