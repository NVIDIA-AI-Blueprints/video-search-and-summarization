// Copyright 2017 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_PUBLIC_PLATFORM_WEB_CLIENT_HINTS_TYPE_H_
#define THIRD_PARTY_BLINK_PUBLIC_PLATFORM_WEB_CLIENT_HINTS_TYPE_H_

#include "services/network/public/mojom/web_client_hints_types.mojom-shared.h"

namespace blink {

// WebEnabledClientHints stores all the client hints along with whether the hint
// is enabled or not.
struct WebEnabledClientHints {
  WebEnabledClientHints() = default;

  bool IsEnabled(network::mojom::WebClientHintsType type) const {
    return enabled_types_[static_cast<int>(type)];
  }
  void SetIsEnabled(network::mojom::WebClientHintsType type, bool should_send) {
    enabled_types_[static_cast<int>(type)] = should_send;
  }

  bool enabled_types_[static_cast<int>(
                          network::mojom::WebClientHintsType::kMaxValue) +
                      1] = {};
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_PUBLIC_PLATFORM_WEB_CLIENT_HINTS_TYPE_H_
