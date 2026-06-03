// Copyright 2017 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_COMMON_FEATURE_POLICY_FEATURE_POLICY_MOJOM_TRAITS_H_
#define THIRD_PARTY_BLINK_COMMON_FEATURE_POLICY_FEATURE_POLICY_MOJOM_TRAITS_H_

#include <map>

#include "base/containers/flat_map.h"
#include "mojo/public/cpp/bindings/enum_traits.h"
#include "third_party/blink/common/feature_policy/policy_value_mojom_traits.h"
#include "third_party/blink/public/common/common_export.h"
#include "third_party/blink/public/common/feature_policy/feature_policy.h"
#include "third_party/blink/public/mojom/feature_policy/feature_policy.mojom-shared.h"
#include "url/mojom/origin_mojom_traits.h"

namespace mojo {

template <>
class BLINK_COMMON_EXPORT
    StructTraits<blink::mojom::ParsedFeaturePolicyDeclarationDataView,
                 blink::ParsedFeaturePolicyDeclaration> {
 public:
  static blink::mojom::FeaturePolicyFeature feature(
      const blink::ParsedFeaturePolicyDeclaration& policy) {
    return policy.feature;
  }
  static const std::vector<url::Origin>& allowed_origins(
      const blink::ParsedFeaturePolicyDeclaration& policy) {
    return policy.allowed_origins;
  }
  static bool fallback_value(
      const blink::ParsedFeaturePolicyDeclaration& policy) {
    return policy.fallback_value;
  }
  static bool opaque_value(
      const blink::ParsedFeaturePolicyDeclaration& policy) {
    return policy.opaque_value;
  }

  static bool Read(blink::mojom::ParsedFeaturePolicyDeclarationDataView in,
                   blink::ParsedFeaturePolicyDeclaration* out);
};

}  // namespace mojo

#endif  // THIRD_PARTY_BLINK_COMMON_FEATURE_POLICY_FEATURE_POLICY_MOJOM_TRAITS_H_
