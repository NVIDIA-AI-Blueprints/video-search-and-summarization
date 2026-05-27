// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "opentelemetry/common/macros.h"
#include "opentelemetry/detail/preprocessor.h"

#define OPENTELEMETRY_ABI_VERSION_NO 1
#define OPENTELEMETRY_VERSION "1.11.0"
#define OPENTELEMETRY_VERSION_MAJOR 1
#define OPENTELEMETRY_VERSION_MINOR 11
#define OPENTELEMETRY_VERSION_PATCH 0

#define OPENTELEMETRY_ABI_VERSION OPENTELEMETRY_STRINGIFY(OPENTELEMETRY_ABI_VERSION_NO)

// clang-format off
#define OPENTELEMETRY_BEGIN_NAMESPACE \
  namespace opentelemetry { inline namespace OPENTELEMETRY_CONCAT(v, OPENTELEMETRY_ABI_VERSION_NO) {

#define OPENTELEMETRY_END_NAMESPACE \
  }}

#define OPENTELEMETRY_NAMESPACE opentelemetry :: OPENTELEMETRY_CONCAT(v, OPENTELEMETRY_ABI_VERSION_NO)

// clang-format on
